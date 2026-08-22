/*
 * tdas_internal_test.c
 *
 * LIVE TEST ONLY: temporarily present one TerraMaster TDAS USB-SCSI device
 * as a Synology SATA port while Synology's sd_probe() runs.
 *
 * Target:
 *   USB path : 2-3.1.4
 *   VID:PID  : 174c:1356
 *   SCSI     : TerraMas / TDAS
 *
 * Designed for:
 *   DSM 7.3.x, epyc7002, kernel 5.10.55, x86_64
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/kprobes.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/usb.h>
#include <linux/device.h>
#include <linux/ptrace.h>

#include <scsi/scsi_device.h>
#include <scsi/scsi_host.h>

#define MODNAME "tdas_internal_test"
#define TARGET_USB_PATH   "2-3.1.4"
#define TARGET_USB_VID    0x174c
#define TARGET_USB_PID    0x1356
#define TARGET_VENDOR     "TerraMas"
#define TARGET_MODEL      "TDAS"

struct probe_ctx {
	struct scsi_device *sdp;
	struct Scsi_Host *host;
	struct scsi_host_template *orig_hostt;
	struct scsi_host_template *clone_hostt;
	bool patched;
};

static bool field_starts_with(const unsigned char *field, const char *wanted)
{
	size_t n = strlen(wanted);
	if (!field)
		return false;
	return strncmp((const char *)field, wanted, n) == 0;
}

static struct usb_device *find_target_usb(struct scsi_device *sdp)
{
	struct device *dev;
	if (!sdp)
		return NULL;
	dev = &sdp->sdev_gendev;
	while (dev) {
		const char *name = dev_name(dev);
		if (dev->bus && dev->bus->name &&
		    strcmp(dev->bus->name, "usb") == 0 &&
		    name && strcmp(name, TARGET_USB_PATH) == 0) {
			struct usb_device *udev = to_usb_device(dev);
			if (le16_to_cpu(udev->descriptor.idVendor) == TARGET_USB_VID &&
			    le16_to_cpu(udev->descriptor.idProduct) == TARGET_USB_PID)
				return udev;
			return NULL;
		}
		dev = dev->parent;
	}
	return NULL;
}

static bool is_target_tdas(struct scsi_device *sdp)
{
	const char *host_name;
	const char *proc_name;
	if (!sdp || !sdp->host || !sdp->host->hostt)
		return false;
	host_name = sdp->host->hostt->name;
	proc_name = sdp->host->hostt->proc_name;
	if (!((host_name &&
	       (!strcmp(host_name, "usb-storage") || !strcmp(host_name, "uas"))) ||
	      (proc_name &&
	       (!strcmp(proc_name, "usb-storage") || !strcmp(proc_name, "uas")))))
		return false;
	if (!field_starts_with(sdp->vendor, TARGET_VENDOR))
		return false;
	if (!field_starts_with(sdp->model, TARGET_MODEL))
		return false;
	if (!find_target_usb(sdp))
		return false;
	return true;
}

static struct device *sd_probe_first_arg(struct pt_regs *regs)
{
#if defined(CONFIG_X86_64)
	return (struct device *)regs->di;
#else
#error "This live-test module is intentionally limited to x86_64."
#endif
}

static int tdas_sd_probe_entry(struct kretprobe_instance *ri,
			       struct pt_regs *regs)
{
	struct probe_ctx *ctx = (struct probe_ctx *)ri->data;
	struct device *dev;
	struct scsi_device *sdp;
	struct scsi_host_template *clone;
	memset(ctx, 0, sizeof(*ctx));
	dev = sd_probe_first_arg(regs);
	if (!dev)
		return 1;
	sdp = to_scsi_device(dev);
	if (!is_target_tdas(sdp))
		return 1;
	if (sdp->host->hostt->syno_port_type == SYNO_PORT_TYPE_SATA) {
		pr_info(MODNAME ": target already has SATA port type; no change\n");
		return 1;
	}
	clone = kmemdup(sdp->host->hostt, sizeof(*clone), GFP_ATOMIC);
	if (!clone) {
		pr_err(MODNAME ": kmemdup(scsi_host_template) failed\n");
		return 1;
	}
	ctx->sdp = sdp;
	ctx->host = sdp->host;
	ctx->orig_hostt = sdp->host->hostt;
	ctx->clone_hostt = clone;
	clone->syno_port_type = SYNO_PORT_TYPE_SATA;
	sdp->host->hostt = clone;
	ctx->patched = true;
	pr_warn(MODNAME
		": ENTER: matched %s/%s VID:PID=%04x:%04x path=%s host%d; "
		"temporary syno_port_type %d -> %d\n",
		sdp->vendor, sdp->model,
		TARGET_USB_VID, TARGET_USB_PID, TARGET_USB_PATH,
		sdp->host->host_no,
		ctx->orig_hostt->syno_port_type,
		clone->syno_port_type);
	return 0;
}

static int tdas_sd_probe_ret(struct kretprobe_instance *ri,
			     struct pt_regs *regs)
{
	struct probe_ctx *ctx = (struct probe_ctx *)ri->data;
	if (!ctx->patched)
		return 0;
	if (ctx->host && ctx->host->hostt == ctx->clone_hostt)
		ctx->host->hostt = ctx->orig_hostt;
	else
		pr_err(MODNAME ": RETURN: host template changed unexpectedly; not overwriting it\n");
	pr_warn(MODNAME
		": RETURN: sd_probe finished for target; restored original USB host template\n");
	kfree(ctx->clone_hostt);
	ctx->clone_hostt = NULL;
	ctx->patched = false;
	return 0;
}

static struct kretprobe tdas_rp = {
	.kp.symbol_name = "sd_probe",
	.entry_handler = tdas_sd_probe_entry,
	.handler = tdas_sd_probe_ret,
	.data_size = sizeof(struct probe_ctx),
	.maxactive = 16,
};

static int __init tdas_internal_init(void)
{
	int ret;
	ret = register_kretprobe(&tdas_rp);
	if (ret < 0) {
		pr_err(MODNAME ": register_kretprobe(sd_probe) failed: %d\n", ret);
		return ret;
	}
	pr_warn(MODNAME
		": loaded; watching sd_probe for TerraMaster TDAS "
		"%04x:%04x at USB %s\n",
		TARGET_USB_VID, TARGET_USB_PID, TARGET_USB_PATH);
	return 0;
}

static void __exit tdas_internal_exit(void)
{
	unregister_kretprobe(&tdas_rp);
	pr_warn(MODNAME ": unloaded; missed=%lu\n",
		(unsigned long)tdas_rp.nmissed);
}

MODULE_LICENSE("GPL");
MODULE_AUTHOR("OpenAI-assisted test module");
MODULE_DESCRIPTION("Live test: temporarily classify one TerraMaster TDAS USB SCSI disk as Synology SATA during sd_probe");
MODULE_VERSION("0.1");
module_init(tdas_internal_init);
module_exit(tdas_internal_exit);
