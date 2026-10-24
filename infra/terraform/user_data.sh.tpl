#!/bin/bash
# Bootstraps a K3s ${role} node. Log: /var/log/k3s-bootstrap.log
set -euxo pipefail
exec > >(tee /var/log/k3s-bootstrap.log) 2>&1

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y curl unzip

# --- kubelet ECR credential provider: lets kubelet pull from ECR with the instance role,
# --- so no imagePullSecrets (ECR tokens expire after 12h).
CRED_DIR=/var/lib/rancher/credentialprovider
mkdir -p $CRED_DIR/bin
curl -fsSL -o $CRED_DIR/bin/ecr-credential-provider \
  https://artifacts.k8s.io/binaries/cloud-provider-aws/${ecr_provider_version}/linux/amd64/ecr-credential-provider-linux-amd64
chmod +x $CRED_DIR/bin/ecr-credential-provider

cat > $CRED_DIR/config.yaml <<'EOF'
apiVersion: kubelet.config.k8s.io/v1
kind: CredentialProviderConfig
providers:
  - name: ecr-credential-provider
    matchImages:
      - "*.dkr.ecr.*.amazonaws.com"
    defaultCacheDuration: "12h"
    apiVersion: credentialprovider.kubelet.k8s.io/v1
EOF

mkdir -p /etc/rancher/k3s
%{ if role == "server" ~}
cat > /etc/rancher/k3s/config.yaml <<'EOF'
write-kubeconfig-mode: "0644"
tls-san:
  - ${public_ip}
kubelet-arg:
  - image-credential-provider-config=/var/lib/rancher/credentialprovider/config.yaml
  - image-credential-provider-bin-dir=/var/lib/rancher/credentialprovider/bin
EOF

curl -sfL https://get.k3s.io | INSTALL_K3S_CHANNEL=${k3s_channel} K3S_TOKEN=${k3s_token} sh -s - server

# Wait for the API, then publish the kubeconfig to S3 so scripts/get-kubeconfig.ps1 can fetch it.
until kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml get nodes >/dev/null 2>&1; do sleep 5; done

curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install
aws s3 cp /etc/rancher/k3s/k3s.yaml ${kubeconfig_s3_uri} --region ${region}
%{ else ~}
cat > /etc/rancher/k3s/config.yaml <<'EOF'
kubelet-arg:
  - image-credential-provider-config=/var/lib/rancher/credentialprovider/config.yaml
  - image-credential-provider-bin-dir=/var/lib/rancher/credentialprovider/bin
EOF

curl -sfL https://get.k3s.io | INSTALL_K3S_CHANNEL=${k3s_channel} K3S_URL=https://${server_ip}:6443 K3S_TOKEN=${k3s_token} sh -s - agent
%{ endif ~}
