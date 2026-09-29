#!/usr/bin/env bash
# FramePort one-time Steam Frame setup. Run in the Frame's Desktop Mode terminal (Konsole):
#   curl -fsSL http://<pc>:<port>/bootstrap.sh | bash
# (the FramePort app shows the exact line and serves this script while its Setup page is open).
#
# What it does (asks before anything that needs sudo):
#   1. sets a password for the 'steamos' user if it has none (sudo needs one)
#   2. enables the SSH server (sshd)
#   3. authorizes the FramePort app's SSH key (no password needed afterwards)
#   4. announces this Frame on the local network (avahi service "_frameport._tcp") so the app finds it
#   5. checks for Valve's Lepton Android runtime and asks Steam to install it if missing
set -euo pipefail
PC_URL="__PC_URL__"          # filled in by the app when serving the script
PAIR_CODE="__PAIR_CODE__"
say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

say "FramePort setup for $(hostname) ($(. /etc/os-release; echo "$NAME $VERSION_ID"))"

if ! passwd -S "$USER" 2>/dev/null | grep -q ' P '; then
    say "Your user has no password yet. SteamOS needs one for sudo (and as an SSH fallback)."
    passwd
fi

say "Enabling the SSH server (sudo)"
sudo systemctl enable --now sshd

say "Authorizing the FramePort app's key"
key=$(curl -fsSL "$PC_URL/key?code=$PAIR_CODE")
[[ "$key" == ssh-ed25519\ * ]] || { echo "Could not fetch the app's key from $PC_URL (is the app still open?)"; exit 1; }
mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
grep -qxF "$key" ~/.ssh/authorized_keys || echo "$key" >> ~/.ssh/authorized_keys

say "Announcing this Frame on the network (avahi)"
svc="<?xml version=\"1.0\" standalone='no'?>
<!DOCTYPE service-group SYSTEM \"avahi-service.dtd\">
<service-group>
  <name replace-wildcards=\"yes\">FramePort on %h</name>
  <service><type>_frameport._tcp</type><port>22</port><txt-record>user=$USER</txt-record><txt-record>pair=$PAIR_CODE</txt-record></service>
</service-group>"
echo "$svc" | sudo tee /etc/avahi/services/frameport.service >/dev/null || true
sudo systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
sudo systemctl reload avahi-daemon >/dev/null 2>&1 || true

say "Checking for Lepton (Valve's Android runtime)"
if ls ~/.local/share/Steam/steamapps/common/Lepton/lepton >/dev/null 2>&1; then
    echo "Lepton found."
else
    echo "Lepton is missing. Make sure Developer Mode is on (Settings > System > Developer)."
    echo "Asking Steam to install it now; confirm in Steam, or launch 'Lepton Development' from your library once."
    (steam steam://install/3029110 >/dev/null 2>&1 &) || true
fi

curl -fsS "$PC_URL/paired?code=$PAIR_CODE&user=$USER&host=$(hostname)" >/dev/null 2>&1 || true
say "Done. Return to FramePort on your PC: this Frame should now appear as connected."
