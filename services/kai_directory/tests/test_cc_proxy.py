import core.cc_extra_routes as cc


def test_directory_proxy_target_constant():
    assert cc.DIRECTORY_BASE == "http://192.168.1.114:8097"
