#!/usr/bin/env bash
# build-live-gnome.sh
# Starts from an empty directory, clones required repos from Gitea,
# configures, and builds a basic Debian GNOME live/installable image.

set -euo pipefail

# ============================================================
# Configuration – edit these
# ============================================================
GITEA_BASE_URL="https://gitea.example.com"   # your Gitea base URL
WORK_ROOT="${PWD}/live-gnome"
DISTRO="trixie"                              # or bookworm, testing, ...
ARCH="amd64"
DEBIAN_MIRROR="http://deb.debian.org/debian/"

# ============================================================
# Helpers
# ============================================================
clone_repo() {
    local path="$1"          # owner/repo  or  full path under Gitea
    local dest="${2:-}"

    if [[ -z "${dest}" ]]; then
        dest=$(basename "${path}" .git)
    fi

    if [[ -d "${dest}/.git" ]]; then
        echo "  already present: ${dest}"
        return
    fi

    echo "  cloning ${path} ..."
    git clone --depth 1 "${GITEA_BASE_URL}/${path}.git" "${dest}"
}

echo "==> Working directory: ${WORK_ROOT}"
mkdir -p "${WORK_ROOT}"
cd "${WORK_ROOT}"

# ============================================================
# 1. Clone the pieces we need from Gitea
# ============================================================
echo
echo "==> Cloning sources from ${GITEA_BASE_URL}"

mkdir -p src
cd src

# Build system
clone_repo "live-team/live-build" "live-build"

# Core components we may later build ourselves or reference
clone_repo "Debian/apt"
clone_repo "dpkg-team/dpkg"
clone_repo "systemd/systemd"
clone_repo "GNOME/gnome-shell"
clone_repo "GNOME/mutter"
clone_repo "GNOME/gdm"
clone_repo "GNOME/gtk"
clone_repo "GNOME/glib"
clone_repo "openssh/openssh-portable" "openssh"
clone_repo "vim/vim"
clone_repo "util-linux/util-linux"
clone_repo "containers/podman"
clone_repo "moby/moby"

# Graphics / audio (useful for later custom packages)
clone_repo "mesa/mesa"
clone_repo "pipewire/pipewire"
clone_repo "pipewire/wireplumber"

cd "${WORK_ROOT}"

# ============================================================
# 2. Install live-build on the host (if missing)
# ============================================================
if ! command -v lb >/dev/null 2>&1; then
    echo
    echo "==> Installing live-build and dependencies on the host"
    sudo apt update
    sudo apt install -y \
        live-build live-boot live-config \
        squashfs-tools xorriso isolinux syslinux-common \
        debootstrap
fi

# Optional: prefer the version cloned from Gitea
# export PATH="${WORK_ROOT}/src/live-build/frontend:${PATH}"

# ============================================================
# 3. Create a clean live-build configuration
# ============================================================
echo
echo "==> Creating live-build configuration"

BUILD_DIR="${WORK_ROOT}/build"
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

lb config \
    --mode debian \
    --system live \
    --distribution "${DISTRO}" \
    --architectures "${ARCH}" \
    --archive-areas "main contrib non-free non-free-firmware" \
    --debian-installer live \
    --binary-images iso-hybrid \
    --bootappend-live "boot=live components quiet splash" \
    --mirror-bootstrap "${DEBIAN_MIRROR}" \
    --mirror-chroot "${DEBIAN_MIRROR}" \
    --mirror-binary "${DEBIAN_MIRROR}" \
    --updates true \
    --security true

# Package selection for a basic but usable GNOME desktop
mkdir -p config/package-lists

cat > config/package-lists/desktop.list.chroot << 'EOF'
# Desktop
task-gnome-desktop
task-desktop

# CLI essentials
openssh-server
vim
bash
coreutils
util-linux
sudo
curl
wget
git

# Audio (PipeWire is the modern default)
pipewire
pipewire-pulse
wireplumber

# Containers
podman
buildah
crun

# Open-source GPU drivers (AMD, Intel, Nouveau via Mesa)
mesa-vulkan-drivers
mesa-utils

# Nice-to-have
network-manager
firefox-esr
EOF

# Optional: later you can point live-build at packages you built
# from the sources under ../src by adding a local apt repository.

echo
echo "==> Configuration ready in ${BUILD_DIR}"

# ============================================================
# 4. Build the image
# ============================================================
echo
echo "==> Starting lb build (requires root, takes a while)..."
sudo lb build

echo
echo "==> Build finished."
echo "    ISO(s) should be in: ${BUILD_DIR}"
ls -lh "${BUILD_DIR}"/*.iso 2>/dev/null || echo "    (no .iso found – check the log above)"

