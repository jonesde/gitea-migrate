#!/usr/bin/env bash
# build-gnome-os.sh
# Starts from an empty directory, clones gnome-build-meta (and useful
# supporting repos) from Gitea, then guides / runs a BuildStream build.

set -euo pipefail

# ============================================================
# Configuration – edit these
# ============================================================
GITEA_BASE_URL="https://gitea.example.com"   # your Gitea base URL
WORK_ROOT="${PWD}/gnome-os-build"

# ============================================================
# Helpers
# ============================================================
clone_repo() {
    local path="$1"
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
# 1. Clone sources from Gitea
# ============================================================
echo
echo "==> Cloning sources from ${GITEA_BASE_URL}"

mkdir -p src
cd src

# The main build metadata project
clone_repo "GNOME/gnome-build-meta"

# Useful supporting / reference repos (optional but handy)
clone_repo "GNOME/gnome-shell"
clone_repo "GNOME/mutter"
clone_repo "GNOME/gdm"
clone_repo "GNOME/gtk"
clone_repo "GNOME/glib"
clone_repo "systemd/systemd"
clone_repo "mesa/mesa"
clone_repo "pipewire/pipewire"
clone_repo "wayland/wayland"
clone_repo "wayland/wayland-protocols"

cd "${WORK_ROOT}"

# ============================================================
# 2. Check / install BuildStream
# ============================================================
echo
if ! command -v bst >/dev/null 2>&1; then
    echo "==> BuildStream (bst) not found."
    echo "    Install it first, for example:"
    echo
    echo "      pip install --user BuildStream"
    echo "      # or follow https://buildstream.build/install.html"
    echo
    echo "    Then re-run this script."
    exit 1
fi

echo "==> Found BuildStream: $(bst --version 2>/dev/null || true)"

# Recommended config (creates a minimal one if missing)
BST_CONF="${HOME}/.config/buildstream.conf"
if [[ ! -f "${BST_CONF}" ]]; then
    echo "==> Creating a basic ~/.config/buildstream.conf"
    mkdir -p "$(dirname "${BST_CONF}")"
    cat > "${BST_CONF}" << 'EOF'
# Basic BuildStream configuration
cache:
  # Optional: point at the public GNOME cache to avoid rebuilding everything
  # (requires network). Comment out for fully offline builds later.
  # storage-service: https://gbm.gnome.org:11003

logging:
  error-logs: true
EOF
fi

# ============================================================
# 3. Enter gnome-build-meta and show useful targets
# ============================================================
META_DIR="${WORK_ROOT}/src/gnome-build-meta"
cd "${META_DIR}"

echo
echo "==> gnome-build-meta is at: ${META_DIR}"
echo
echo "Common BuildStream targets in this project:"
echo "  vm/image.bst          – GNOME OS VM / disk image"
echo "  iso/image.bst         – ISO image (when available)"
echo "  sdk/sdk.bst           – Flatpak SDK"
echo "  sdk/platform.bst      – Flatpak platform runtime"
echo "  core/gnome-shell.bst  – just GNOME Shell + deps"
echo

# ============================================================
# 4. Build (choose one)
# ============================================================
# Uncomment the target you want. The full image build is heavy.

TARGET="${1:-vm/image.bst}"   # allow override: ./build-gnome-os.sh core/gnome-shell.bst

echo "==> Building target: ${TARGET}"
echo "    (This can take a long time and a lot of disk/CPU on the first run)"
echo

bst build "${TARGET}"

echo
echo "==> Build of ${TARGET} finished."
echo
echo "Useful follow-up commands (run from ${META_DIR}):"
echo
echo "  # Check out the artifact so you can inspect or use it"
echo "  bst artifact checkout ${TARGET} --directory ~/gnome-os-artifact"
echo
echo "  # Or get a shell inside the built environment"
echo "  bst shell ${TARGET}"
echo
echo "  # For an ISO-style target you can often do:"
echo "  bst artifact checkout --hardlinks iso/image.bst"
echo
echo "See the docs under ${META_DIR}/docs/ for turning artifacts"
echo "into a bootable image and for contributing changes."

