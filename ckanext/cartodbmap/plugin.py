import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
import cartodb_client
import ckan.lib.helpers as h
import ckan.lib.datapreview as datapreview
import ckan.logic as ckanlogic

import json
import requests, json

ignore_missing = plugins.toolkit.get_validator('ignore_missing')

# per https://carto.com/docs/carto-engine/import-api/importing-geospatial-data/#supported-geospatial-data-formats
CARTODB_FORMATS = ['csv','tsv','kml','kmz','xls', 'xlsx', 'geojson', 'gpx', 'osm', 'bz2', 'ods', 'zip', '.zip', 'gz', 'tgz']

# Create New CartoDB Client
cc = cartodb_client.CartoDBClient()

def set_cartodb_username(username,context):
    if not username and context.get('auth_user_obj').sysadmin:
        username = h.config.get('ckanext.cartodbmap.cartodb.username')
        
    cc.username = username
    '''
    if not cc.username:
        message = plugins.toolkit._('Missing Username')
        raise plugins.toolkit.Invalid(message)
    '''
    cc.cartodb_url = 'https://'+ cc.username +'.carto.com'
    return
      
def set_cartodb_key(key,context):
    if not key and context.get('auth_user_obj').sysadmin:
        key = h.config.get('ckanext.cartodbmap.cartodb.key')
        
    cc.api_key = key
    '''
    if not cc.api_key:
        message = plugins.toolkit._('Missing API Key')
        raise plugins.toolkit.Invalid(message)
    '''
    return


def get_discourse_url():
    suffix = h.config.get('ckanext.cartodbmap.discourse.suffix',None)
    if suffix:
        discourse_url = h.config.get('discourse.url').rstrip('\/')
        
        try:
            pkg_dict = plugins.toolkit.c.__getattr__("pkg_dict")
            package_name = pkg_dict.get("name")
        except:
            package_name = plugins.toolkit.c.__getattr__("id")
        
        discourse_url += "/t/"
        discourse_url += package_name
        discourse_url += "-"
        discourse_url += suffix
        return discourse_url
    return

def set_datastore_limit():
    """
    Returns the row limit for datastore resources
    """
    try:
        row_limit = h.config.get('ckanext.cartodbmap.datastore.limit',100000).replace(',','')
        row_limit = long(row_limit)
    except:
        row_limit = 100000
    return row_limit

def get_datastore_count(resource_id):
    """
    Given a resource_id of a DataStore resource, return the row count
    """
    try:
        datastore = toolkit.get_action('datastore_info')(None, {'id': resource_id})
        if datastore.get('meta', False):
            meta = datastore.get('meta', False)
            if meta.get('count', False):
                datastore_count = meta.get('count')
                datastore_count = long(datastore_count)
    except:
        datastore_count = 0
    return datastore_count

def vis_from_resource(url,context):
    # Create new CartoDB Vis if url field is empty
    if not url:
        if not (cc.api_key and cc.username):
            message = plugins.toolkit._('Missing Carto Username/API Key')
            raise plugins.toolkit.Invalid(message)
        
        # Get resource url
        resource_id = plugins.toolkit.c.__getattr__("resource_id")
        resource = toolkit.get_action('resource_show')(context,{'id': resource_id})
        if resource["url"].startswith('/datastore/dump/'):
            row_limit = set_datastore_limit()
            row_count = get_datastore_count(resource_id)
            if long(row_count) < long(row_limit):
                host = h.config.get('ckan.site_url')
                while host.endswith('/'):
                    host = host[:-1]
                resource_url = host + resource["url"]
            else:
                message = plugins.toolkit._('DataStore resource is too large, limit is set to ' + str(row_limit) + ' rows')
                raise plugins.toolkit.Invalid(message)
        else:
            resource_url = resource["url"]
        resource_format_lower = resource["format"].lower()
        
        # Check if CartoDB accepts current file format
        if not (resource_format_lower in CARTODB_FORMATS):
            message = plugins.toolkit._('Unsupported Carto file format: ' + resource_format_lower)
            raise plugins.toolkit.Invalid(message)
        
        cartodb_obj = cc.create_cartodb_resource_view(resource_url)
        if(cartodb_obj["success"]):
            try:
                pkg_dict = plugins.toolkit.c.__getattr__("pkg_dict")
                package_id = pkg_dict.get("id")
                create_bounding_box(context,package_id,cartodb_obj['response']['table_name'])
            except:
                print "Failed creating bounding box."
            return cartodb_obj['response']["cartodb_vis_url"]
        else:
            message = plugins.toolkit._('Unable to create visualization: ' + cartodb_obj["messages"]["user_message"])
            print json.dumps(cartodb_obj, indent=4, sort_keys=True)
            raise plugins.toolkit.Invalid(message)
    return url

def create_bounding_box(context,package_id,table_name):
    package_dict = plugins.toolkit.get_action('package_show')(context, {'id' : package_id})
    print json.dumps(package_dict, indent=4, sort_keys=True)
    spatial_field_exists = False
    for extra in package_dict.get('extras'):
        if extra.get('key') == 'spatial':
            spatial_field_exists = True
    # Create Bounding Box extra field if it doesn't exist
    if not spatial_field_exists:
        resource_dict = {
            "q" : "SELECT ST_AsText(ST_Extent(the_geom)) as table_extent FROM " + table_name,
            "api_key" : cc.api_key
        }
        r = requests.post(cc.cartodb_url + "/api/v2/sql"
                        ,data=resource_dict
                        ,headers={
                            "Content-Type" : "application/x-www-form-urlencoded"
                        }
                    )
        bbox = r.json().get('rows',[None])[0].get('table_extent')
        
        bbox_str = "{'type':'Polygon','coordinates': [[[" + bbox.replace('POLYGON((','').replace('))','').replace(',','],[').replace(' ',',') + "]]]}"
        bbox_str = bbox_str.replace("'",'"')
        
        package_dict['extras'] += [{"key":"spatial", "value":bbox_str}]
        print json.dumps(package_dict, indent=4, sort_keys=True)
        ckanlogic.get_action('package_update')(context, package_dict)

class CartodbmapPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer, inherit=True)
    plugins.implements(plugins.IResourceView, inherit=True)
    
    # IResourceController is needed if you need to auto generate a view once a resource is created.
    plugins.implements(plugins.IResourceController, inherit=True)
    
    # Declare that this plugin will implement ITemplateHelpers.
    plugins.implements(plugins.ITemplateHelpers)


    # IConfigurer
    def update_config(self, config_):
        toolkit.add_public_directory(config_, 'theme/public')
        toolkit.add_template_directory(config_, 'theme/templates')
        toolkit.add_resource('theme/public', 'cartodbmap')
        
    # ITemplateHelpers
    def get_helpers(self):
        '''Register the get_discourse_url() function above as a template
        helper function.

        '''
        # Template helper function names should begin with the name of the
        # extension they belong to, to avoid clashing with functions from
        # other extensions.
        return {'ckanext_cartodbmap_get_discourse_url': get_discourse_url}
        
    # IResourceView
    def info(self):
        schema = {
            'cartodb_account': [ignore_missing,set_cartodb_username],
            'cartodb_key'    : [ignore_missing,set_cartodb_key],
            'cartodb_vis_url': [ignore_missing,vis_from_resource]
        }

        return {
            'name': 'cartodb-map',
            'title': 'Carto Map',
            'icon': 'compass',
            'schema': schema,
            'iframed': False,
        }
    
    def can_view(self, data_dict):
        return True
    
    def setup_template_variables(self, context, data_dict):
        resource = data_dict['resource']
        resource_view = data_dict['resource_view']
        resource_url = data_dict['resource']['url']
        return {
            'resource': resource,
            'resource_view': resource_view,
        }
    
    def view_template(self, context, data_dict):
        return 'cartodbmap_view.html'

    def form_template(self, context, data_dict):
        # Set default view name to Carto View
        if(not 'title' in data_dict["resource_view"]):
            data_dict["resource_view"]["title"] = "Carto View"
        return 'cartodbmap_form.html'
    
    # IResourceController
    def add_default_cartodb_view(self, context, data_dict):
        try:
            resource = data_dict
            if resource.get('format').lower() == 'geojson':
                cc.username = h.config.get('ckanext.cartodbmap.cartodb.username')
                cc.cartodb_url = 'https://'+ cc.username +'.carto.com'
                cc.api_key = h.config.get('ckanext.cartodbmap.cartodb.key')
                
                
                cartodb_obj = cc.create_cartodb_resource_view(resource["url"])
                if(not cartodb_obj["success"]):
                    message = plugins.toolkit._('Unable to create visualization: ' + cartodb_obj["messages"]["user_message"])
                    print json.dumps(cartodb_obj, indent=4, sort_keys=True)
                    raise plugins.toolkit.Invalid(message)
                
                view = {
                    'title': 'Carto View',
                    # detect when it is a service, not a file
                    'description': 'Carto View of the GeoJSON file',
                    'resource_id': resource['id'],
                    'view_type': 'cartodb-map',
                    'cartodb_vis_url' : cartodb_obj['response']['cartodb_vis_url']
                }
                ckanlogic.get_action('resource_view_create')(context,view)
                try:
                    package_id = plugins.toolkit.c.__getattr__("id")
                    create_bounding_box(context,package_id,cartodb_obj['response']['table_name'])
                except:
                    print "Failed creating bounding box."
        except:
            print "!!!! Warning:: Unable create default Carto view"
                
    def after_create(self, context, data_dict):
        self.add_default_cartodb_view(context, data_dict)
