"""
providers - pluggable data-source adapters, one subpackage per domain.

    providers.chargers   charging networks (BaseChargerProvider), queried many at
                         once and merged by ChargerDiscoveryManager
    providers.power      power retailer/distributor billing (BasePowerProvider),
                         one active at a time via get_power_provider()
    providers.pv         PV monitoring (BasePvProvider), one active at a time via
                         get_pv_provider()

Import the subpackage you need (`from providers.power import get_power_provider`);
this file imports nothing, so loading one domain never loads the others.
"""
