# Copyright 2016, 2017 Red Hat, Inc.
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import ConfigParser
import sys

from constants import LOG
from oslo_config import cfg
import tempest.config


class TempestConf(ConfigParser.SafeConfigParser):
    # causes the config parser to preserve case of the options
    optionxform = str

    # set of pairs `(section, key)` which have a higher priority (are
    # user-defined) and will usually not be overwritten by `set()`
    priority_sectionkeys = set()

    CONF = tempest.config.TempestConfigPrivate(parse_conf=False)

    def get_bool_value(self, value):
        """Returns boolean value of the string value given.

        :param value: True/False
        :type value: String
        :returns: Boolean value of the string value given
        :rtype: Boolean
        """
        strval = str(value).lower()
        if strval == 'true':
            return True
        elif strval == 'false':
            return False
        else:
            raise ValueError("'%s' is not a boolean" % value)

    def get_defaulted(self, section, key):
        """Returns default value for the section.key pair.

        :param section: a section in a tempest.conf file
        :type section: String
        :param key: a key in a section in a tempest.conf file
        :type key: String
        :returns: default value for the section.key pair
        :rtype: String
        """
        try:
            if self.has_option(section, key):
                return self.get(section, key)
            else:
                return self.CONF.get(section).get(key)
        except cfg.NoSuchOptError:
            LOG.warning("Option %s is not defined in %s section", key, section)

    def set(self, section, key, value, priority=False):
        """Set value in configuration, similar to `SafeConfigParser.set`

        Creates non-existent sections. Keeps track of options which were
        specified by the user and should not be normally overwritten.

        :param section: a section in a tempest.conf file
        :type section: String
        :param key: a key in a section in a tempest.conf file
        :type key: String
        :param value: a value to be set to the section.key
        :type value: String
        :param priority: if True, always over-write the value. If False, don't
            over-write an existing value if it was written before with a
            priority (i.e. if it was specified by the user)
        :type priority: Boolean
        :returns: True if the value was written, False if not (because of
            priority)
        :rtype: Boolean
        """
        if not self.has_section(section) and section.lower() != "default":
            self.add_section(section)
        if not priority and (section, key) in self.priority_sectionkeys:
            LOG.debug("Option '[%s] %s = %s' was defined by user, NOT"
                      " overwriting into value '%s'", section, key,
                      self.get(section, key), value)
            return False
        if priority:
            self.priority_sectionkeys.add((section, key))
        LOG.debug("Setting [%s] %s = %s", section, key, value)
        ConfigParser.SafeConfigParser.set(self, section, key, value)
        return True

    def remove_values(self, to_remove):
        """Remove values from configuration file specified in arguments.

        :param to_remove: {'section.key': [values_to_be_removed], ...}
        :type to_remove: dict
        """
        for key_path in to_remove:
            section, key = key_path.split('.')
            try:
                conf_values = self.get(section, key).split(',')
                remove = to_remove[key_path]
                if len(remove) == 0:
                    # delete all values in section.key
                    self.remove_option(section, key)
                elif len(conf_values) == 1:
                    # make sure only the value specified by user
                    # will be deleted if in the key is other value
                    # than expected, ignore it
                    if conf_values[0] in to_remove[key_path]:
                        self.remove_option(section, key)
                else:
                    # exclude all unwanted values from the list
                    # and preserve the original order of items
                    conf_values = [v for v in conf_values if v not in remove]
                    self.set(section, key, ",".join(conf_values))
            except ConfigParser.NoOptionError:
                # only inform a user, option specified by him doesn't exist
                LOG.error(sys.exc_info()[1])
            except ConfigParser.NoSectionError:
                # only inform a user, section specified by him doesn't exist
                LOG.error(sys.exc_info()[1])
