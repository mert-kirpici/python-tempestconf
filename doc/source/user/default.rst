==============
Default values
==============

``python-tempestconf`` provides sensitive default values for many options in
order to simplify its usage, reducing the amount of options that needs to be
specified.

Here is the list of tempest options, which are set by default:

.. code-block:: ini

    [DEFAULT]
    debug = true
    use_stderr = false
    log_file = tempest.log

    [identity]
    username = demo_tempestconf
    password = secrete
    project_name = demo
    alt_username = alt_demo_tempestconf
    alt_password = secrete
    alt_project_name = alt_demo

    [auth]
    ; if member role is not present tempest_roles option is not set
    tempest_roles = member
    admin_username = admin
    admin_project_name = admin
    admin_domain_name = Default

    [object-storage]
    reseller_admin_role = ResellerAdmin

    [oslo-concurrency]
    lock_path = /tmp

    [compute-feature-enabled]
    # Default deployment does not use shared storage
    preserve_ports = true

    [network-feature-enabled]
    ipv6_subnet_attributes = true

