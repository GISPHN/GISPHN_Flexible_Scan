def classFactory(iface):
    from .plugin import FlexibleScanPlugin
    return FlexibleScanPlugin(iface)
