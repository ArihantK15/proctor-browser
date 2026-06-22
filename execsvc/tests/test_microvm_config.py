from execsvc.microvm import build_vm_config


def test_config_has_no_network_and_readonly_root():
    cfg = build_vm_config(rootfs="rootfs.ext4", kernel="vmlinux")
    assert "network-interfaces" not in cfg
    assert cfg["drives"][0]["is_read_only"] is True
