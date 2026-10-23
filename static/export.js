(function () {
    const root = document.querySelector("[data-publisher-export]");
    if (!root) return;

    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});

    const dialog = root.querySelector("dialog");
    const form = root.querySelector("form");
    const result = root.querySelector("[data-publisher-result]");
    const resultStatus = root.querySelector("[data-publisher-result-status]");
    const resultUrl = root.querySelector("[data-publisher-result-url]");
    const openResult = root.querySelector("[data-open-publisher-result]");
    const copyResult = root.querySelector("[data-copy-publisher-result]");
    const stopTunnel = root.querySelector("[data-stop-publisher]");
    const nameInput = root.querySelector("[data-publisher-name]");
    const modeInputs = root.querySelectorAll("[name='publisher-mode']");
    const provider = root.querySelector("[data-publisher-provider]");
    const help = root.querySelector("[data-publisher-help]");
    const currentUrl = window.location.href;
    let pollTimer;

    if (!dialog || !form || !result || !resultStatus || !resultUrl) return;

    function selectedMode() {
        return root.querySelector("[name='publisher-mode']:checked").value;
    }

    function updateHelp() {
        const mode = selectedMode();
        const providerName = provider.value;
        if (mode === "app") {
            help.textContent = "Génère une application web installable (PWA), utilisable sur Windows, Android, iPhone, macOS et Linux depuis un navigateur compatible.";
            provider.hidden = true;
        } else {
            provider.hidden = false;
            help.textContent = providerName === "cloudflare"
                ? "Commande Cloudflare Tunnel : cloudflared tunnel --url http://127.0.0.1:5001. Le lien HTTPS public sera fourni par Cloudflare."
                : providerName === "ngrok"
                    ? "Commande ngrok : ngrok http 5001. ngrok affichera une URL HTTPS publique vers cette page."
                    : `Lien actuel : ${currentUrl}. Pour un autre appareil sur le même réseau, utilisez l’adresse IP de cet ordinateur.`;
        }
    }

    root.querySelector("button[data-open-publisher]").addEventListener("click", () => {
        nameInput.value = root.dataset.publisherDefaultName || document.title.replace(" - DowFlow", "");
        form.hidden = false;
        result.hidden = true;
        dialog.showModal();
        updateHelp();
    });
    modeInputs.forEach((input) => input.addEventListener("change", updateHelp));
    provider.addEventListener("change", updateHelp);

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const name = nameInput.value.trim() || "DowFlow";
        const mode = selectedMode();
        const scope = root.dataset.publisherScope || "root";
        result.hidden = false;
        openResult.hidden = true;
        copyResult.hidden = true;
        if (stopTunnel) stopTunnel.hidden = true;
        resultUrl.textContent = "";

        const localPath = scope === "root" ? "/" : `/${scope}`;
        const namedUrl = (baseUrl) => `${baseUrl}${baseUrl.includes("?") ? "&" : "?"}export=${encodeURIComponent(name)}`;
        const localUrl = namedUrl(`${window.location.origin}${localPath}`);
        if (mode === "app") {
            const manifestUrl = `${window.location.origin}/manifest.webmanifest?scope=${encodeURIComponent(scope)}&name=${encodeURIComponent(name)}`;
            resultStatus.textContent = `Application « ${name} » prête à être installée depuis cet espace.`;
            resultUrl.textContent = localUrl;
            openResult.href = localUrl;
            openResult.hidden = false;
            copyResult.hidden = false;
            copyResult.onclick = () => navigator.clipboard.writeText(localUrl);
            return;
        }
        if (provider.value === "local" || provider.value === "other") {
            resultStatus.textContent = `Lien « ${name} » prêt.`;
            resultUrl.textContent = localUrl;
            openResult.href = localUrl;
            openResult.hidden = false;
            copyResult.hidden = false;
            copyResult.onclick = () => navigator.clipboard.writeText(localUrl);
            return;
        }
        resultStatus.textContent = `Démarrage automatique de ${provider.value}…`;
        const response = await fetch("/api/publisher/tunnel/start", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({provider: provider.value, scope})
        });
        const state = await response.json();
        if (!state.success) {
            resultStatus.textContent = state.error;
            return;
        }
        const refresh = async () => {
            const statusResponse = await fetch("/api/publisher/tunnel/status");
            const status = await statusResponse.json();
            if (status.ready) {
                resultStatus.textContent = `Lien « ${name} » prêt.`;
                const publicUrl = namedUrl(status.url);
                resultUrl.textContent = publicUrl;
                openResult.href = publicUrl;
                openResult.hidden = false;
                copyResult.hidden = false;
                if (stopTunnel) stopTunnel.hidden = false;
                copyResult.onclick = () => navigator.clipboard.writeText(publicUrl);
                clearInterval(pollTimer);
            } else if (status.error) {
                resultStatus.textContent = status.error;
                clearInterval(pollTimer);
            }
        };
        await refresh();
        pollTimer = setInterval(refresh, 1000);
    });
    if (stopTunnel) stopTunnel.addEventListener("click", async () => {
        await fetch("/api/publisher/tunnel/stop", {method: "POST"});
        clearInterval(pollTimer);
        resultStatus.textContent = "Tunnel arrêté.";
        openResult.hidden = true;
        copyResult.hidden = true;
        if (stopTunnel) stopTunnel.hidden = true;
    });
}());