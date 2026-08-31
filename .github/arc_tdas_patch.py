#!/usr/bin/env python3
from pathlib import Path

p = Path("arc-lkm/shim/storage/sata_port_shim.c")
s = p.read_text()


def repl(old: str, new: str, label: str) -> None:
    global s
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {n}")
    s = s.replace(old, new, 1)


repl(
    '#include <linux/ctype.h> //isprint()\n',
    '#include <linux/ctype.h> //isprint()\n'
    '#include <linux/device.h> //devm_kmemdup()\n'
    '#include <linux/string.h>\n',
    'includes',
)

repl(
    '#define VIRTIO_HOST_ID "Virtio SCSI HBA"\n',
    '#define VIRTIO_HOST_ID "Virtio SCSI HBA"\n\n'
    '/* TerraMaster F8 internal TDAS bridge: physical USB topology path. */\n'
    '#define USB_INTERNAL_PATH "2-3.1.4"\n'
    '#define USB_INTERNAL_ATA_PORT 0\n',
    'constants',
)

anchor = '''    return false;\n}\n\n#if RP_HAS_SYNO_BLOCK_INFO\nstatic const char *resolve_syno_pciepath(struct device *pci_dev, char *out_buf, size_t out_len)\n'''
insert = '''    return false;\n}\n\n/*\n * Match only the SCSI host below the physical TerraMaster TDAS USB path.\n * dev_name() on the parent chain contains both "2-3.1.4" and\n * "2-3.1.4:1.0" (the UAS interface).\n */\nstatic bool is_usb_internal_target(const struct scsi_device *sdp)\n{\n    struct device *dev;\n    const size_t path_len = strlen(USB_INTERNAL_PATH);\n\n    if (unlikely(!sdp || !sdp->host))\n        return false;\n\n    dev = sdp->host->shost_gendev.parent;\n    while (dev) {\n        const char *name = dev_name(dev);\n\n        if (name &&\n            (!strcmp(name, USB_INTERNAL_PATH) ||\n             (!strncmp(name, USB_INTERNAL_PATH, path_len) && name[path_len] == ':')))\n            return true;\n\n        dev = dev->parent;\n    }\n\n    return false;\n}\n\n/*\n * Never modify the global UAS/usb-storage host template.  Clone it for the\n * target Scsi_Host only, so the Arc boot USB (1-3/host0) and all other USB\n * storage keep their original Synology port type.\n */\nstatic int prepare_usb_internal_host(struct scsi_device *sdp)\n{\n    struct scsi_host_template *private_hostt;\n\n    if (!is_usb_internal_target(sdp))\n        return 0;\n\n    if (sdp->host->hostt->syno_port_type == SYNO_PORT_TYPE_SATA)\n        return 0;\n\n    private_hostt = devm_kmemdup(&sdp->host->shost_gendev,\n                                 sdp->host->hostt,\n                                 sizeof(*private_hostt),\n                                 GFP_KERNEL);\n    if (unlikely(!private_hostt)) {\n        pr_loc_err("USB internal: failed to clone SCSI host template for path %s",\n                   USB_INTERNAL_PATH);\n        return -ENOMEM;\n    }\n\n    private_hostt->syno_port_type = SYNO_PORT_TYPE_SATA;\n    sdp->host->hostt = private_hostt;\n\n    pr_loc_inf("USB internal: host%d path=%s cloned and changed to SATA port type",\n               sdp->host->host_no, USB_INTERNAL_PATH);\n    return 0;\n}\n\n#if RP_HAS_SYNO_BLOCK_INFO\nstatic const char *resolve_syno_pciepath(struct device *pci_dev, char *out_buf, size_t out_len)\n'''
repl(anchor, insert, 'target helper insertion')

repl(
    '    char dts_pciepath[128];\n\n    if (!current_config.hw_config || !current_config.hw_config->is_dt)\n',
    '    char dts_pciepath[128];\n'
    '    const bool usb_internal = is_usb_internal_target(sdp);\n\n'
    '    if (!current_config.hw_config || !current_config.hw_config->is_dt)\n',
    'usb_internal local',
)

repl(
    '    if (host_uses_libata(sdp->host) || !host_pci_parent_is_storage(sdp->host))\n'
    '        return;\n',
    '    if (host_uses_libata(sdp->host) ||\n'
    '        (!usb_internal && !host_pci_parent_is_storage(sdp->host)))\n'
    '        return;\n',
    'PCI class guard',
)

repl(
    "    if (sdp->syno_block_info[0] != '\\0')\n        return;\n",
    "    if (!usb_internal && sdp->syno_block_info[0] != '\\0')\n        return;\n",
    'block info overwrite guard',
)

old_port = '''    ata_port_no = sdp->id;\n    if (!resolve_hba_port_no(sdp, &ata_port_no))\n        pr_loc_dbg("No port-H:P node for /dev/%s; falling back to sdp->id=%u",\n                   sdp->syno_disk_name, sdp->id);\n\n    pciepath = resolve_syno_pciepath(dev, dts_pciepath, sizeof(dts_pciepath));\n'''
new_port = '''    if (usb_internal) {\n        /*\n         * Use the xHCI PCI BDF as a stable synthetic controller.  The Arc DT\n         * disks addon consumes pciepath/ata_port_no/driver for slot mapping;\n         * this does not require the physical controller to be AHCI.\n         */\n        ata_port_no = USB_INTERNAL_ATA_PORT;\n        pciepath = dev_name(dev);\n    } else {\n        ata_port_no = sdp->id;\n        if (!resolve_hba_port_no(sdp, &ata_port_no))\n            pr_loc_dbg("No port-H:P node for /dev/%s; falling back to sdp->id=%u",\n                       sdp->syno_disk_name, sdp->id);\n\n        pciepath = resolve_syno_pciepath(dev, dts_pciepath, sizeof(dts_pciepath));\n    }\n'''
repl(old_port, new_port, 'synthetic USB block info')

repl(
    '    if (unlikely(!sdp || !sdp->host || !sdp->host->hostt))\n'
    '        return false;\n\n'
    '    const char *host_name = sdp->host->hostt->name;\n',
    '    if (unlikely(!sdp || !sdp->host || !sdp->host->hostt))\n'
    '        return false;\n\n'
    '    if (is_usb_internal_target(sdp))\n'
    '        return true;\n\n'
    '    const char *host_name = sdp->host->hostt->name;\n',
    'is_fixable target',
)

repl(
    'static int on_new_scsi_disk_device(struct scsi_device *sdp)\n'
    '{\n'
    '    if (!is_fixable(sdp))\n'
    '        return 0;\n\n',
    'static int on_new_scsi_disk_device(struct scsi_device *sdp)\n'
    '{\n'
    '    int out;\n\n'
    '    if (!is_fixable(sdp))\n'
    '        return 0;\n\n'
    '    if (is_usb_internal_target(sdp)) {\n'
    '        out = prepare_usb_internal_host(sdp);\n'
    '        if (unlikely(out != 0))\n'
    '            return out;\n'
    '    }\n\n',
    'on_new target prep',
)

repl(
    'static int on_existing_scsi_disk_device(struct scsi_device *sdp)\n'
    '{\n'
    '    if (!is_fixable(sdp))\n'
    '        return 0;\n\n',
    'static int on_existing_scsi_disk_device(struct scsi_device *sdp)\n'
    '{\n'
    '    int out;\n\n'
    '    if (!is_fixable(sdp))\n'
    '        return 0;\n\n'
    '    if (is_usb_internal_target(sdp)) {\n'
    '        out = prepare_usb_internal_host(sdp);\n'
    '        if (unlikely(out != 0))\n'
    '            return out;\n'
    '    }\n\n',
    'on_existing target prep',
)

p.write_text(s)
print(f"Patched {p}")
