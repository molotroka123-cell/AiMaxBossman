#!/usr/bin/env bash
# Этап 0, шаги 1.3–1.7: базовая настройка хоста (Fedora Server 43, Strix Halo / gfx1151).
# Запускать по секциям, читая вывод. Не «один раз и забыть».
set -euo pipefail

echo "=== 1.3 пакеты и группы ==="
sudo dnf upgrade -y
sudo dnf install -y git curl jq htop btop nvtop rocminfo rocm-smi mesa-vulkan-drivers vulkan-tools nut
sudo usermod -aG video,render "$USER"
echo "ядро: $(uname -r)  (нужно >= 6.14)"
vulkaninfo --summary 2>/dev/null | grep -i -E "deviceName|8060" || echo "!! Vulkan не видит GPU — проверь драйверы/ядро"
rocminfo 2>/dev/null | grep -m1 gfx || echo "!! rocminfo не видит gfx1151 (Vulkan всё равно работает)"

echo "=== 1.4 память под GPU (потолок, не резерв) ==="
# ~117 ГБ GTT для iGPU. В BIOS UMA Frame Buffer оставить минимальным (512 МБ–2 ГБ).
sudo grubby --update-kernel=ALL --args="amdgpu.gttsize=120000 ttm.pages_limit=30720000 ttm.page_pool_size=30720000"
echo "после перезагрузки проверить: cat /sys/class/drm/card*/device/mem_info_gtt_total"

echo "=== 1.5 Docker ==="
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager addrepo --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo || true
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

echo "=== 1.6 Tailscale + периметр ==="
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh --hostname bossman
# всё, что не Tailscale и не localhost — закрыто
sudo firewall-cmd --permanent --zone=trusted --add-interface=tailscale0
sudo firewall-cmd --permanent --zone=public --remove-service=ssh || true
sudo firewall-cmd --permanent --zone=public --set-target=DROP
sudo firewall-cmd --reload
sudo sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart sshd

echo "=== 1.7 авторазблокировка LUKS через TPM2 (чтобы сервер перезагружался без монитора) ==="
LUKS_DEV=$(lsblk -o NAME,FSTYPE -rn | awk '$2=="crypto_LUKS"{print "/dev/"$1; exit}')
echo "LUKS-раздел: ${LUKS_DEV:-не найден}"
if [ -n "${LUKS_DEV:-}" ]; then
  sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7 "$LUKS_DEV"
  sudo sed -i 's/\(^luks-[^ ]* .* none \)\(.*\)$/\1\2,tpm2-device=auto/' /etc/crypttab
  sudo dracut -f
  echo "проверка: sudo reboot → система должна подняться без ввода пароля"
fi

echo "=== готово. Перелогиниться (группы docker/render/video) и перезагрузиться. ==="
