"""bootstrap.sh: valid bash, placeholders for the pairing server, and the config.vdf edit that turns on Developer
Mode."""
import re
import shutil
import subprocess
import sys

import pytest

from frameport.core.paths import bootstrap_dir

SCRIPT = (bootstrap_dir() / "bootstrap.sh").read_text()


def devmode_edit() -> str:
    return re.search(r"python3 - \"\$CONFIG\" <<'PY'[^\n]*\n(.*?)\nPY\n", SCRIPT, re.S).group(1)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_bash_syntax():
    subprocess.run(["bash", "-n"], input=SCRIPT, text=True, check=True)


def test_placeholders_and_steps():
    assert "__PC_URL__" in SCRIPT and "__PAIR_CODE__" in SCRIPT
    assert "steamos-devkit-mode" in SCRIPT and "/etc/steamos-devkit-enabled" in SCRIPT
    assert "sudo" not in SCRIPT.split("set -euo pipefail", 1)[1] and "passwd" not in SCRIPT  # no password, ever
    # Desktop Mode is a nested session inside steam.service: the Developer Mode job (it stops Steam) must run as a
    # user unit reached through the real user bus, logging to a file that outlives the terminal
    assert 'DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"' in SCRIPT
    assert "user_systemd systemd-run --user" in SCRIPT and ".cache/frameport-setup.log" in SCRIPT


@pytest.mark.parametrize("before", [
    '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t}\n\t}\n'
    '\t"developer"\n\t{\n\t\t"Other"\t\t"x"\n\t}\n}\n',
    '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t}\n}\n',
    '"InstallConfigStore"\n{\n\t"developer"\n\t{\n\t\t"DevModeEnabled"\t\t"0"\n\t}\n}\n',
])
def test_devmode_config_edit(tmp_path, before):
    cfg = tmp_path / "config.vdf"
    cfg.write_text(before)
    subprocess.run([sys.executable, "-", str(cfg)], input=devmode_edit(), text=True, check=True)
    after = cfg.read_text()
    assert re.search(r'\t"developer"\n\t\{\n(?:.*\n)*?\t\t"DevModeEnabled"\t\t"1"\n', after)
    assert after.count("DevModeEnabled") == 1 and after.count("{") == after.count("}")
