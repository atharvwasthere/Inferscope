# Deploying inferscope to self-hosted k3s on Azure

[← back to README](../README.md)

This is the exact path used to take inferscope from local Docker to a live, self-hosted
Kubernetes cluster at **https://inferscope.atharvsingh.me**. It includes the things that
actually broke, so you can skip them.

For the *why* behind the architecture (reverse proxy, migrate Job, 12-factor config), see
[DEPLOYMENT.md](../DEPLOYMENT.md). This doc is the *how*.

```
your laptop                         Azure VM (Ubuntu 24.04, B2ms)
┌──────────────┐  docker push   ┌────────────────────────────────────────┐
│ build images │ ─────────────► │ Docker Hub: atharv19/inferscope-*        │
└──────────────┘                │                                          │
                                │  k3s ── pulls images ── runs all pods:   │
visitor                         │   postgres redis ingestion chatbot       │
  │  https://inferscope...      │   dashboard frontend                     │
  ▼                             │                                          │
Cloudflare ──HTTP:80──► Traefik ─► Ingress ─► frontend(nginx) ─► /api/* ─► chatbot/dashboard
                                └────────────────────────────────────────┘
```

---

## 0. Prerequisites
- Docker + a Docker Hub account (here: `atharv19`).
- An Azure subscription (here: Azure for Students, $100 credit).
- A domain on Cloudflare (here: `atharvsingh.me`).

## 1. Build and push the images
Run on your machine (it has Docker). The script builds with the `backend/` context (where
`sdk/`, `obs/`, `alembic/` live) and the `frontend/` context, tags them `atharv19/inferscope-*`,
and pushes.
```bash
docker login -u atharv19            # paste a Docker Hub access token, not your password
DOCKER_USER=atharv19 bash scripts/build_push.sh
```
Make the four Docker Hub repos **public** so k3s can pull without a pull secret.

> **Gotcha — `make` on Windows.** `make push` fails with `command not found` on Windows (no make).
> Run the script directly as above. On the Linux VM, `make` is available after `apt-get install make`.

## 2. Create the Azure VM
Portal → Virtual machines → Create:
- **Image** Ubuntu Server 24.04 LTS · **Size** `Standard_B2ms` (2 vCPU / **8 GB**) · **Auth** SSH public key (`azureuser`, generate + download the `.pem`).
- **Inbound ports**: allow **SSH (22)** only.
- **OS disk**: Standard SSD. **Auto-shutdown**: on (saves credit).

> **Gotcha — size.** B1s (1 GB) cannot run this. k3s alone eats ~1 GB; postgres + redis + 3 Python
> services + nginx need ~2.5–3 GB. B2ms (8 GB) is comfortable; B2s (4 GB) is the tight minimum.

After it boots: VM → Network settings → click the public IP → **Configuration → Static**, so the IP
survives stop/start. (Ours: `20.193.128.46`.)

> **Cost.** B2ms is ~$0.09/hr, not the "$65" the portal shows — that's the *monthly* rate at 24/7.
> A demo costs a few dollars. **Stop (deallocate)** the VM when idle to halt compute billing.

## 3. SSH in
```powershell
icacls "C:\path\to\inferscope-key.pem" /inheritance:r /grant:r "$($env:USERNAME):R"
ssh -F NUL -i "C:\path\to\inferscope-key.pem" azureuser@20.193.128.46
```
> **Gotcha — `Bad owner or permissions on ~/.ssh/config`.** A broken global SSH config blocks the
> connection before the key is even used. `-F NUL` tells SSH to ignore the config file. (Permanent
> fix: `takeown` + `icacls` on `~/.ssh/config`.)

## 4. Install k3s (on the VM)
```bash
curl -sfL https://get.k3s.io | sh -
sudo k3s kubectl get nodes           # node should be Ready in seconds
```
Make `kubectl` usable without sudo:
```bash
mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown $USER ~/.kube/config
export KUBECONFIG=$HOME/.kube/config
echo 'export KUBECONFIG=$HOME/.kube/config' >> ~/.bashrc
kubectl get nodes
```
> **Gotcha — `permission denied` on `/etc/rancher/k3s/k3s.yaml`.** Plain `kubectl` defaults to the
> root-owned kubeconfig. Copying it to `~/.kube/config` and exporting `KUBECONFIG` fixes it.

## 5. Clone the repo and fill secrets
```bash
sudo apt-get update -q && sudo apt-get install -y make
git clone https://github.com/atharvwasthere/Lumino.git && cd Lumino
nano k8s/secret.yaml          # replace REPLACE_ME with real AWS / GOOGLE / GROQ keys
```
`AWS_REGION` is non-secret and already lives in `k8s/configmap.yaml` — only the keys go in the Secret.
If you have no AWS keys, leave them as `REPLACE_ME`: Bedrock won't work but groq/gemini will (the AWS
client is created lazily, so the chatbot won't crash).

> Secrets only ever exist on the VM. `k8s/secret.yaml` in git stays `REPLACE_ME`.

## 6. Deploy
```bash
make deploy
```
Applies in dependency order: namespace → configmap → secret → postgres (wait) → redis (wait) →
**migrate Job** (`alembic upgrade head`, waits for completion) → ingestion → chatbot → dashboard →
frontend → ingress. Then:
```bash
kubectl get pods -n llmobs           # all Running; migrate = Completed
```

## 7. Expose the app port (NodePort 30000)
The `frontend` service is a NodePort on **30000**. Open it in the VM's Network Security Group:
Portal → VM → Network settings → add inbound rule → **Destination port 30000, TCP, Source Any, Allow**.

Now `http://20.193.128.46:30000` loads the app.

> **Gotcha — wrong port / wrong field.** Two easy slips: putting the port in *Source port ranges*
> (leave that `*`) instead of *Destination*, and typing the wrong number (8080 vs 30000). The
> destination port must be **30000**.

## 8. Custom domain + HTTPS via Cloudflare
The Ingress (`k8s/ingress.yaml`) routes the hostname through Traefik (k3s's built-in ingress on port
80). Cloudflare gives free HTTPS with no certs on the VM.

1. **Cloudflare → DNS → add A record:** `inferscope` → `20.193.128.46`, **Proxied (orange cloud)**.
2. **Cloudflare → SSL/TLS → Overview → mode = Flexible** (visitor↔CF is HTTPS; CF↔VM is HTTP on :80).
3. **NSG: open port 80** (TCP, Source Any, Allow) so Cloudflare can reach Traefik.

```
visitor ──HTTPS──► Cloudflare ──HTTP:80──► Traefik ──► Ingress ──► frontend:3000
```
Result: **https://inferscope.atharvsingh.me** with a valid cert. The `:30000` NodePort URL keeps
working too.

> **Gotcha — Cloudflare 521/522.** Means CF can't reach the origin on :80 → the NSG port-80 rule is
> missing. Flexible mode needs origin on plain HTTP:80, which is what Traefik serves.
>
> Want CF↔origin encrypted too? Switch to **Full** and install a free Cloudflare **Origin
> Certificate** on Traefik. Optional; Flexible is fine for a demo.

## 9. Day-to-day
- **Stop the VM** when not demoing: Portal → VM → Stop (deallocate). Static IP + disk persist.
- **Restart** before a demo; pods come back automatically (k3s service is enabled at boot).
- **Redeploy after a code change:** `bash scripts/build_push.sh` locally, then on the VM
  `kubectl rollout restart deploy -n llmobs` (re-pulls `:latest`).
- **Tear down everything:** delete the Azure resource group `inferscope-rg`.
