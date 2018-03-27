# Copyright 2016 Red Hat, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""
This script will generate the etc/tempest.conf file by applying a series of
specified options in the following order:

1. Values from etc/default-overrides.conf, if present. This file will be
provided by the distributor of the tempest code, a distro for example, to
specify defaults that are different than the generic defaults for tempest.

2. Values using the file provided by the --deployer-input argument to the
script.
Some required options differ among deployed clouds but the right values cannot
be discovered by the user. The file used here could be created by an installer,
or manually if necessary.

3. Values provided in client's cloud config file or as an environment
variables, see documentation of os-client-config
https://docs.openstack.org/developer/os-client-config/

4. Values provided on the command line. These override all other values.

5. Discovery. Values that have not been provided in steps [2-4] will be
obtained by querying the cloud.
"""

import api_discovery
import argparse
import ConfigParser
import logging
import os
import shutil
import sys
import urllib2

from clients import ClientManager
from credentials import Credentials
import os_client_config
from oslo_config import cfg
from tempest.lib import exceptions
import tempest_conf

LOG = logging.getLogger(__name__)

# Get the current tempest workspace path
TEMPEST_WORKSPACE = os.getcwd()

DEFAULTS_FILE = os.path.join(TEMPEST_WORKSPACE, "etc",
                             "default-overrides.conf")
DEFAULT_IMAGE = ("http://download.cirros-cloud.net/0.3.5/"
                 "cirros-0.3.5-x86_64-disk.img")
DEFAULT_IMAGE_FORMAT = 'qcow2'

# services and their codenames
SERVICE_NAMES = {
    'baremetal': 'ironic',
    'compute': 'nova',
    'database': 'trove',
    'data-processing': 'sahara',
    'image': 'glance',
    'network': 'neutron',
    'object-store': 'swift',
    'orchestration': 'heat',
    'share': 'manila',
    'telemetry': 'ceilometer',
    'volume': 'cinder',
    'messaging': 'zaqar',
    'metric': 'gnocchi',
    'event': 'panko',
}

# what API versions could the service have and should be enabled/disabled
# depending on whether they get discovered as supported. Services with only one
# version don't need to be here, neither do service versions that are not
# configurable in tempest.conf
SERVICE_VERSIONS = {
    'image': {'supported_versions': ['v1', 'v2'], 'catalog': 'image'},
    'identity': {'supported_versions': ['v2', 'v3'], 'catalog': 'identity'},
    'volume': {'supported_versions': ['v2', 'v3'], 'catalog': 'volumev3'}
}

# Keep track of where the extensions are saved for that service.
# This is necessary because the configuration file is inconsistent - it uses
# different option names for service extension depending on the service.
SERVICE_EXTENSION_KEY = {
    'compute': 'api_extensions',
    'object-store': 'discoverable_apis',
    'network': 'api_extensions',
    'volume': 'api_extensions',
    'identity': 'api_extensions'
}


def set_logging(debug, verbose):
    """Set logging based on the arguments.

    :type debug: Boolean
    :type verbose: Boolean
    """
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    logging.basicConfig(format=log_format)
    if debug:
        LOG.setLevel(logging.DEBUG)
    elif verbose:
        LOG.setLevel(logging.INFO)


def read_deployer_input(deployer_input_file, conf):
    """Read deployer-input file and set values in conf accordingly.

    :param deployer_input_file: Path to the deployer inut file
    :type deployer_input_file: String
    :param conf: TempestConf object
    """
    LOG.info("Adding options from deployer-input file '%s'",
             deployer_input_file)
    deployer_input = ConfigParser.SafeConfigParser()
    deployer_input.read(deployer_input_file)
    for section in deployer_input.sections():
        # There are no deployer input options in DEFAULT
        for (key, value) in deployer_input.items(section):
            conf.set(section, key, value, priority=True)


def set_options(conf, deployer_input, non_admin, overrides=[],
                test_accounts=None, cloud_creds=None):
    """Set options in conf provided by different source.

    1. read the default values in default-overrides file
    2. read a file provided by --deployer-input argument
    3. set values from client's config (os-client-config support) if provided
    4. set overrides - may override values which were set in the steps above

    :param conf: TempestConf object
    :param deployer_input: Path to the deployer inut file
    :type deployer_input: string
    :type non_admin: boolean
    :param overrides: list of tuples: [(section, key, value)]
    :type overrides: list
    :param test_accounts: Path to the accounts.yaml file
    :type test_accounts: string
    :param cloud_creds: Cloud credentials from client's config
    :type cloud_creds: dict
    """
    if os.path.isfile(DEFAULTS_FILE):
        LOG.info("Reading defaults from file '%s'", DEFAULTS_FILE)
        conf.read(DEFAULTS_FILE)

    if deployer_input and os.path.isfile(deployer_input):
        read_deployer_input(deployer_input, conf)

    if non_admin:
        # non admin, so delete auth admin values which were set
        # from default-overides.conf file
        conf.set("auth", "admin_username", "")
        conf.set("auth", "admin_project_name", "")
        conf.set("auth", "admin_password", "")
        # To maintain backward compatibilty
        # Moved to auth
        conf.set("identity", "admin_username", "")
        # To maintain backward compatibility
        # renamed as admin_project_name in auth section
        conf.set("identity", "admin_tenant_name", "")
        # To maintain backward compatibility
        # Moved to auth
        conf.set("identity", "admin_password", "")
        conf.set("auth", "use_dynamic_credentials", "False")

    # get and set auth data from client's config
    if cloud_creds:
        set_cloud_config_values(non_admin, cloud_creds, conf)

    if test_accounts:
        # new way for running using accounts file
        conf.set("auth", "use_dynamic_credentials", "False")
        conf.set("auth", "test_accounts_file",
                 os.path.abspath(test_accounts))

    # set overrides - values specified in CLI
    for section, key, value in overrides:
        conf.set(section, key, value, priority=True)


def parse_arguments():
    cloud_config = os_client_config.OpenStackConfig()
    parser = argparse.ArgumentParser(__doc__)
    cloud_config.register_argparse_arguments(parser, sys.argv)
    parser.add_argument('--create', action='store_true', default=False,
                        help='create default tempest resources')
    parser.add_argument('--out', default="etc/tempest.conf",
                        help='the tempest.conf file to write')
    parser.add_argument('--deployer-input', default=None,
                        help="""A file in the format of tempest.conf that will
                                override the default values. The
                                deployer-input file is an alternative to
                                providing key/value pairs. If there are also
                                key/value pairs they will be applied after the
                                deployer-input file.
                        """)
    parser.add_argument('overrides', nargs='*', default=[],
                        help="""key value pairs to modify. The key is
                                section.key where section is a section header
                                in the conf file.
                                For example: identity.username myname
                                 identity.password mypass""")
    parser.add_argument('--debug', action='store_true', default=False,
                        help='Print debugging information')
    parser.add_argument('--verbose', '-v', action='store_true', default=False,
                        help='Print more information about the execution')
    parser.add_argument('--non-admin', action='store_true', default=False,
                        help='Run without admin creds')
    parser.add_argument('--test-accounts', default=None, metavar='PATH',
                        help='Use accounts from accounts.yaml')
    parser.add_argument('--image-disk-format', default=DEFAULT_IMAGE_FORMAT,
                        help="""a format of an image to be uploaded to glance.
                                Default is '%s'""" % DEFAULT_IMAGE_FORMAT)
    parser.add_argument('--image', default=DEFAULT_IMAGE,
                        help="""an image to be uploaded to glance. The name of
                                the image is the leaf name of the path which
                                can be either a filename or url. Default is
                                '%s'""" % DEFAULT_IMAGE)
    parser.add_argument('--network-id',
                        help="""The ID of an existing network in our openstack
                                instance with external connectivity""")
    parser.add_argument('--remove', action='append', default=[],
                        metavar="SECTION.KEY=VALUE[,VALUE]",
                        help="""key value pair to be removed from
                                configuration file.
                                For example: --remove identity.username=myname
                                --remove feature-enabled.api_ext=http,https""")

    args = parser.parse_args()
    if args.create and args.non_admin:
        raise Exception("Options '--create' and '--non-admin' cannot be used"
                        " together, since creating" " resources requires"
                        " admin rights")
    args.overrides = parse_overrides(args.overrides)
    args.remove = parse_values_to_remove(args.remove)
    cloud = cloud_config.get_one_cloud(argparse=args)
    return cloud


def parse_values_to_remove(options):
    """Manual parsing of remove arguments.

    :param options: list of arguments following --remove argument
    :return: dictionary containing key paths with values to be removed
    :rtype: dict
    EXAMPLE: {'identity.username': [myname],
              'identity-feature-enabled.api_extensions': [http, https]}
    """
    parsed = {}
    for argument in options:
        if len(argument.split('=')) == 2:
            section, values = argument.split('=')
            if len(section.split('.')) != 2:
                raise Exception("Missing dot. The option --remove has to"
                                "come in the format 'section.key=value,"
                                " but got '%s'." % (argument))
            parsed[section] = values.split(',')
        else:
            # missing equal sign, all values in section.key will be deleted
            parsed[argument] = []
    return parsed


def parse_overrides(overrides):
    """Manual parsing of positional arguments.

    :param overrides: list of section.keys and values to override, example:
               ['section.key', 'value', 'section.key', 'value']
    :return: list of tuples, example: [('section', 'key', 'value'), ...]
    :rtype: list
    """
    if len(overrides) % 2 != 0:
        raise Exception("An odd number of override options was found. The"
                        " overrides have to be in 'section.key value' format.")
    i = 0
    new_overrides = []
    while i < len(overrides):
        section_key = overrides[i].split('.')
        value = overrides[i + 1]
        if len(section_key) != 2:
            raise Exception("Missing dot. The option overrides has to come in"
                            " the format 'section.key value', but got '%s'."
                            % (overrides[i] + ' ' + value))
        section, key = section_key
        new_overrides.append((section, key, value))
        i += 2
    return new_overrides


def set_cloud_config_values(non_admin, cloud_creds, conf):
    """Set values from client's cloud config file.

    Set admin and non-admin credentials and uri from cloud credentials.
    Note: the values may be later overridden by values specified in CLI.

    :type non_admin: Boolean
    :param cloud_creds: auth data from os-client-config
    :type cloud_creds: dict
    :param conf: TempestConf object
    """
    try:
        if non_admin:
            conf.set('identity', 'username', cloud_creds['username'])
            conf.set('identity',
                     'tenant_name',
                     cloud_creds['project_name'])
            conf.set('identity', 'password', cloud_creds['password'])
        else:
            conf.set('identity', 'admin_username', cloud_creds['username'])
            conf.set('identity',
                     'admin_tenant_name',
                     cloud_creds['project_name'])
            conf.set('identity', 'admin_password', cloud_creds['password'])
        conf.set('identity', 'uri', cloud_creds['auth_url'])

    except cfg.NoSuchOptError:
        LOG.warning(
            'Could not load some identity options from cloud config file')


def create_tempest_users(tenants_client, roles_client, users_client, conf,
                         services):
    """Create users necessary for Tempest if they don't exist already."""
    create_user_with_tenant(tenants_client, users_client,
                            conf.get('identity', 'username'),
                            conf.get('identity', 'password'),
                            conf.get('identity', 'tenant_name'))

    username = conf.get_defaulted('auth', 'admin_username')
    if username is None:
        username = conf.get_defaulted('identity', 'admin_username')
    give_role_to_user(tenants_client, roles_client, users_client,
                      username,
                      conf.get('identity', 'tenant_name'), role_name='admin')

    # Prior to juno, and with earlier juno defaults, users needed to have
    # the heat_stack_owner role to use heat stack apis. We assign that role
    # to the user if the role is present.
    if 'orchestration' in services:
        give_role_to_user(tenants_client, roles_client, users_client,
                          conf.get('identity', 'username'),
                          conf.get('identity', 'tenant_name'),
                          role_name='heat_stack_owner',
                          role_required=False)

    create_user_with_tenant(tenants_client, users_client,
                            conf.get('identity', 'alt_username'),
                            conf.get('identity', 'alt_password'),
                            conf.get('identity', 'alt_tenant_name'))


def give_role_to_user(tenants_client, roles_client, users_client, username,
                      tenant_name, role_name, role_required=True):
    """Give the user a role in the project (tenant).""",
    tenant_id = tenants_client.get_project_by_name(tenant_name)['id']
    users = users_client.list_users()
    user_ids = [u['id'] for u in users['users'] if u['name'] == username]
    user_id = user_ids[0]
    roles = roles_client.list_roles()
    role_ids = [r['id'] for r in roles['roles'] if r['name'] == role_name]
    if not role_ids:
        if role_required:
            raise Exception("required role %s not found" % role_name)
        LOG.debug("%s role not required", role_name)
        return
    role_id = role_ids[0]
    try:
        roles_client.create_user_role_on_project(tenant_id, user_id, role_id)
        LOG.debug("User '%s' was given the '%s' role in project '%s'",
                  username, role_name, tenant_name)
    except exceptions.Conflict:
        LOG.debug("(no change) User '%s' already has the '%s' role in"
                  " project '%s'", username, role_name, tenant_name)


def create_user_with_tenant(tenants_client, users_client, username,
                            password, tenant_name):
    """Create a user and a tenant if it doesn't exist."""

    LOG.info("Creating user '%s' with tenant '%s' and password '%s'",
             username, tenant_name, password)
    tenant_description = "Tenant for Tempest %s user" % username
    email = "%s@test.com" % username
    # create a tenant
    try:
        tenants_client.create_project(name=tenant_name,
                                      description=tenant_description)
    except exceptions.Conflict:
        LOG.info("(no change) Tenant '%s' already exists", tenant_name)

    tenant_id = tenants_client.get_project_by_name(tenant_name)['id']

    # create a user
    try:
        users_client.create_user(**{'name': username, 'password': password,
                                    'tenantId': tenant_id, 'email': email})
    except exceptions.Conflict:
        LOG.info("User '%s' already exists.", username)


def create_tempest_flavors(client, conf, allow_creation):
    """Find or create flavors 'm1.nano' and 'm1.micro' and set them in conf.

    If 'flavor_ref' and 'flavor_ref_alt' are specified in conf, it will first
    try to find those - otherwise it will try finding or creating 'm1.nano' and
    'm1.micro' and overwrite those options in conf.

    :param allow_creation: if False, fail if flavors were not found
    """
    # m1.nano flavor
    flavor_id = None
    if conf.has_option('compute', 'flavor_ref'):
        flavor_id = conf.get('compute', 'flavor_ref')
    flavor_id = find_or_create_flavor(client,
                                      flavor_id, 'm1.nano',
                                      allow_creation, ram=64)
    conf.set('compute', 'flavor_ref', flavor_id)

    # m1.micro flavor
    alt_flavor_id = None
    if conf.has_option('compute', 'flavor_ref_alt'):
        alt_flavor_id = conf.get('compute', 'flavor_ref_alt')
    alt_flavor_id = find_or_create_flavor(client,
                                          alt_flavor_id, 'm1.micro',
                                          allow_creation, ram=128)
    conf.set('compute', 'flavor_ref_alt', alt_flavor_id)


def find_or_create_flavor(client, flavor_id, flavor_name,
                          allow_creation, ram=64, vcpus=1, disk=0):
    """Try finding flavor by ID or name, create if not found.

    :param flavor_id: first try finding the flavor by this
    :param flavor_name: find by this if it was not found by ID, create new
        flavor with this name if not found at all
    :param allow_creation: if False, fail if flavors were not found
    :param ram: memory of created flavor in MB
    :param vcpus: number of VCPUs for the flavor
    :param disk: size of disk for flavor in GB
    """
    flavor = None
    flavors = client.list_flavors()['flavors']
    # try finding it by the ID first
    if flavor_id:
        found = [f for f in flavors if f['id'] == flavor_id]
        if found:
            flavor = found[0]
    # if not found previously, try finding it by name
    if flavor_name and not flavor:
        found = [f for f in flavors if f['name'] == flavor_name]
        if found:
            flavor = found[0]

    if not flavor and not allow_creation:
        raise Exception("Flavor '%s' not found, but resource creation"
                        " isn't allowed. Either use '--create' or provide"
                        " an existing flavor" % flavor_name)

    if not flavor:
        LOG.info("Creating flavor '%s'", flavor_name)
        flavor = client.create_flavor(name=flavor_name,
                                      ram=ram, vcpus=vcpus,
                                      disk=disk, id=None)
        return flavor['flavor']['id']
    else:
        LOG.info("(no change) Found flavor '%s'", flavor['name'])

    return flavor['id']


def create_tempest_images(client, conf, image_path, allow_creation,
                          disk_format):
    img_path = os.path.join(conf.get("scenario", "img_dir"),
                            os.path.basename(image_path))
    name = image_path[image_path.rfind('/') + 1:]
    conf.set('scenario', 'img_file', name)
    alt_name = name + "_alt"
    image_id = None
    if conf.has_option('compute', 'image_ref'):
        image_id = conf.get('compute', 'image_ref')
    image_id = find_or_upload_image(client,
                                    image_id, name, allow_creation,
                                    image_source=image_path,
                                    image_dest=img_path,
                                    disk_format=disk_format)
    alt_image_id = None
    if conf.has_option('compute', 'image_ref_alt'):
        alt_image_id = conf.get('compute', 'image_ref_alt')
    alt_image_id = find_or_upload_image(client,
                                        alt_image_id, alt_name, allow_creation,
                                        image_source=image_path,
                                        image_dest=img_path,
                                        disk_format=disk_format)

    conf.set('compute', 'image_ref', image_id)
    conf.set('compute', 'image_ref_alt', alt_image_id)


def check_ceilometer_service(client, conf, services):
    services = client.list_services(**{'type': 'metering'})
    if services and len(services['services']):
        metering = services['services'][0]
        if 'ceilometer' in metering['name'] and metering['enabled']:
            conf.set('service_available', 'ceilometer', 'True')


def check_volume_backup_service(client, conf, services):
    """Verify if the cinder backup service is enabled"""
    if 'volumev3' not in services:
        LOG.info("No volume service found, skipping backup service check")
        return
    params = {'binary': 'cinder-backup'}
    backup_service = client.list_services(**params)

    if backup_service:
        # We only set backup to false if the service isn't running otherwise we
        # keep the default value
        service = backup_service['services']
        if not service or service[0]['state'] == 'down':
            conf.set('volume-feature-enabled', 'backup', 'False')


def find_or_upload_image(client, image_id, image_name, allow_creation,
                         image_source='', image_dest='', disk_format=''):
    image = _find_image(client, image_id, image_name)
    if not image and not allow_creation:
        raise Exception("Image '%s' not found, but resource creation"
                        " isn't allowed. Either use '--create' or provide"
                        " an existing image_ref" % image_name)

    if image:
        LOG.info("(no change) Found image '%s'", image['name'])
        path = os.path.abspath(image_dest)
        if not os.path.isfile(path):
            _download_image(client, image['id'], path)
    else:
        LOG.info("Creating image '%s'", image_name)
        if image_source.startswith("http:") or \
           image_source.startswith("https:"):
                _download_file(image_source, image_dest)
        else:
            shutil.copyfile(image_source, image_dest)
        image = _upload_image(client, image_name, image_dest, disk_format)
    return image['id']


def create_tempest_networks(clients, conf, has_neutron, public_network_id):
    label = None
    public_network_name = None
    # TODO(tkammer): separate logic to different func of Nova network
    # vs Neutron
    if has_neutron:
        client = clients.get_neutron_client()

        # if user supplied the network we should use
        if public_network_id:
            LOG.info("Looking for existing network id: {0}"
                     "".format(public_network_id))

            # check if network exists
            network_list = client.list_networks()
            for network in network_list['networks']:
                if network['id'] == public_network_id:
                    public_network_name = network['name']
                    break
            else:
                raise ValueError('provided network id: {0} was not found.'
                                 ''.format(public_network_id))

        # no network id provided, try to auto discover a public network
        else:
            LOG.info("No network supplied, trying auto discover for network")
            network_list = client.list_networks()
            for network in network_list['networks']:
                if network['router:external'] and network['subnets']:
                    LOG.info("Found network, using: {0}".format(network['id']))
                    public_network_id = network['id']
                    public_network_name = network['name']
                    break

            # Couldn't find an existing external network
            else:
                LOG.error("No external networks found. "
                          "Please note that any test that relies on external "
                          "connectivity would most likely fail.")

        if public_network_id is not None:
            conf.set('network', 'public_network_id', public_network_id)
        if public_network_name is not None:
            conf.set('network', 'floating_network_name', public_network_name)

    else:
        client = clients.get_nova_net_client()
        networks = client.list_networks()
        if networks:
            label = networks['networks'][0]['label']

    if label:
        conf.set('compute', 'fixed_network_name', label)
    elif not has_neutron:
        raise Exception('fixed_network_name could not be discovered and'
                        ' must be specified')


def configure_keystone_feature_flags(conf, services):
    """Set keystone feature flags based upon version ID."""
    supported_versions = services.get('identity', {}).get('versions', [])
    if len(supported_versions) <= 1:
        return
    for version in supported_versions:
        major, minor = version.split('.')[:2]
        # Enable the domain specific roles feature flag. For more information,
        # see https://developer.openstack.org/api-ref/identity/v3
        if major == 'v3' and int(minor) >= 6:
            conf.set('identity-feature-enabled',
                     'forbid_global_implied_dsr',
                     'True')


def configure_boto(conf, services):
    """Set boto URLs based on discovered APIs."""
    if 'ec2' in services:
        conf.set('boto', 'ec2_url', services['ec2']['url'])
    if 's3' in services:
        conf.set('boto', 's3_url', services['s3']['url'])


def configure_horizon(conf):
    """Derive the horizon URIs from the identity's URI."""
    uri = conf.get('identity', 'uri')
    u = urllib2.urlparse.urlparse(uri)
    base = '%s://%s%s' % (u.scheme, u.netloc.replace(
        ':' + str(u.port), ''), '/dashboard')
    assert base.startswith('http:') or base.startswith('https:')
    has_horizon = True
    try:
        urllib2.urlopen(base)
    except urllib2.URLError:
        has_horizon = False
    conf.set('service_available', 'horizon', str(has_horizon))
    conf.set('dashboard', 'dashboard_url', base + '/')
    conf.set('dashboard', 'login_url', base + '/auth/login/')


def configure_discovered_services(conf, services):
    """Set service availability and supported extensions and versions.

    Set True/False per service in the [service_available] section of `conf`
    depending of wheter it is in services. In the [<service>-feature-enabled]
    section, set extensions and versions found in `services`.

    :param conf: ConfigParser configuration
    :param services: dictionary of discovered services - expects each service
        to have a dictionary containing 'extensions' and 'versions' keys
    """
    # check if volume service is disabled
    if conf.has_section('services') and conf.has_option('services', 'volume'):
        if not conf.getboolean('services', 'volume'):
            SERVICE_NAMES.pop('volume')
            SERVICE_VERSIONS.pop('volume')
    # set service availability
    for service, codename in SERVICE_NAMES.iteritems():
        # ceilometer is still transitioning from metering to telemetry
        if service == 'telemetry' and 'metering' in services:
            service = 'metering'
        conf.set('service_available', codename, str(service in services))

    # TODO(arxcruz): Remove this once/if we get the following reviews merged
    # in all branches supported by tempestconf, or once/if tempestconf do not
    # support anymore the OpenStack release where those patches are not
    # available.
    # https://review.openstack.org/#/c/492526/
    # https://review.openstack.org/#/c/492525/

    if 'alarming' in services:
        conf.set('service_available', 'aodh', 'True')
        conf.set('service_available', 'aodh_plugin', 'True')

    # set supported API versions for services with more of them
    for service, service_info in SERVICE_VERSIONS.iteritems():
        supported_versions = services.get(
            service_info['catalog'], {}).get('versions', [])
        section = service + '-feature-enabled'
        for version in service_info['supported_versions']:
            is_supported = any(version in item
                               for item in supported_versions)
            conf.set(section, 'api_' + version, str(is_supported))

    # set service extensions
    keystone_v3_support = conf.get('identity-feature-enabled', 'api_v3')
    for service, ext_key in SERVICE_EXTENSION_KEY.iteritems():
        if service in services:
            extensions = ','.join(services[service].get('extensions', ""))
            if service == 'object-store':
                # tempest.conf is inconsistent and uses 'object-store' for the
                # catalog name but 'object-storage-feature-enabled'
                service = 'object-storage'
            elif service == 'identity' and keystone_v3_support:
                identity_v3_ext = api_discovery.get_identity_v3_extensions(
                    conf.get("identity", "uri_v3"))
                extensions = list(set(extensions.split(',') + identity_v3_ext))
                extensions = ','.join(extensions)
            conf.set(service + '-feature-enabled', ext_key, extensions)


def _download_file(url, destination):
    if os.path.exists(destination):
        LOG.info("Image '%s' already fetched to '%s'.", url, destination)
        return
    LOG.info("Downloading '%s' and saving as '%s'", url, destination)
    f = urllib2.urlopen(url)
    data = f.read()
    with open(destination, "wb") as dest:
        dest.write(data)


def _download_image(client, id, path):
    """Download file from glance."""
    LOG.info("Downloading image %s to %s", id, path)
    body = client.show_image_file(id)
    LOG.debug(type(body.data))
    with open(path, 'wb') as out:
        out.write(body.data)


def _upload_image(client, name, path, disk_format):
    """Upload image file from `path` into Glance with `name."""
    LOG.info("Uploading image '%s' from '%s'", name, os.path.abspath(path))

    with open(path) as data:
        image = client.create_image(name=name,
                                    disk_format=disk_format,
                                    container_format='bare',
                                    visibility="public")
        client.store_image_file(image['id'], data)
        return image


def _find_image(client, image_id, image_name):
    """Find image by ID or name (the image client doesn't have this)."""
    if image_id:
        try:
            return client.show_image(image_id)
        except exceptions.NotFound:
            pass
    found = filter(lambda x: x['name'] == image_name,
                   client.list_images()['images'])
    if found:
        return found[0]
    else:
        return None


def main():
    args = parse_arguments()
    set_logging(args.debug, args.verbose)

    conf = tempest_conf.TempestConf()
    cloud_creds = args.config.get('auth')
    set_options(conf, args.deployer_input, args.non_admin,
                args.overrides, args.test_accounts, cloud_creds)

    uri = conf.get("identity", "uri")
    api_version = 2
    if "v3" in uri:
        api_version = 3
        conf.set("identity", "auth_version", "v3")
        conf.set("identity", "uri_v3", uri)
    else:
        # TODO(arxcruz) make a check if v3 is enabled
        conf.set("identity", "uri_v3", uri.replace("v2.0", "v3"))
    credentials = Credentials(conf, not args.non_admin)
    clients = ClientManager(conf, credentials)
    swift_discover = conf.get_defaulted('object-storage-feature-enabled',
                                        'discoverability')
    services = api_discovery.discover(
        clients.auth_provider,
        clients.identity_region,
        object_store_discovery=conf.get_bool_value(swift_discover),
        api_version=api_version,
        disable_ssl_certificate_validation=conf.get_defaulted(
            'identity',
            'disable_ssl_certificate_validation'
        )
    )
    if args.create and args.test_accounts is None:
        create_tempest_users(clients.tenants, clients.roles, clients.users,
                             conf, services)
    create_tempest_flavors(clients.flavors, conf, args.create)
    create_tempest_images(clients.images, conf, args.image, args.create,
                          args.image_disk_format)
    has_neutron = "network" in services

    LOG.info("Setting up network")
    LOG.debug("Is neutron present: {0}".format(has_neutron))
    create_tempest_networks(clients, conf, has_neutron, args.network_id)

    configure_discovered_services(conf, services)
    check_volume_backup_service(clients.volume_service, conf, services)
    check_ceilometer_service(clients.service_client, conf, services)
    configure_boto(conf, services)
    configure_keystone_feature_flags(conf, services)
    configure_horizon(conf)

    # remove all unwanted values if were specified
    if args.remove != {}:
        LOG.info("Removing configuration: %s", str(args.remove))
        conf.remove_values(args.remove)
    LOG.info("Creating configuration file %s", os.path.abspath(args.out))
    with open(args.out, 'w') as f:
        conf.write(f)


if __name__ == "__main__":
    main()