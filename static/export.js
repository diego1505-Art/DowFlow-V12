(function () {
    const root = document.querySelector("[data-publisher-export]");
    if (!root) return;

    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});

    const dialog = root.querySelector("dialog");
    const form = root.querySelector("form");
    const nameInput = root.querySelector("[data-publisher-name]");
    const modeInputs = root.querySelectorAll("[name='publisher-mode']");
    const provider = root.querySelector("[data-publisher-provider]");
    const help = root.querySelector("[data-publisher-help]");
    const currentUrl = window.location.href;

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
        dialog.showModal();
        updateHelp();
    });
    modeInputs.forEach((input) => input.addEventListener("change", updateHelp));
    provider.addEventListener("change", updateHelp);

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const name = nameInput.value.trim() || "DowFlow";
        const mode = selectedMode();
        const scope = root.dataset.publisherScope || "root";
        if (mode === "app") {
            window.location.href = `/api/publisher/app?scope=${encodeURIComponent(scope)}&name=${encodeURIComponent(name)}`;
        } else {
            window.location.href = `/api/publisher/link?scope=${encodeURIComponent(scope)}&name=${encodeURIComponent(name)}&provider=${encodeURIComponent(provider.value)}`;
        }
        dialog.close();
    });
}());