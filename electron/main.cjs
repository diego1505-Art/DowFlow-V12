const { app, BrowserWindow, dialog } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");

const serverUrl = "http://127.0.0.1:5001";
let serverProcess;

function startFlask() {
    const python = process.env.DOWFLOW_PYTHON || (process.platform === "win32" ? "python" : "python3");
    serverProcess = spawn(python, ["app.py"], {
        cwd: app.isPackaged ? path.join(process.resourcesPath, "dowflow") : path.resolve(__dirname, ".."),
        env: { ...process.env, DOWFLOW_HOST: "127.0.0.1" },
        stdio: "ignore",
        windowsHide: true,
    });
    serverProcess.on("error", (error) => {
        dialog.showErrorBox("DowFlow", `Impossible de démarrer Python : ${error.message}`);
    });
}

async function waitForServer() {
    for (let attempt = 0; attempt < 30; attempt += 1) {
        try {
            const response = await fetch(serverUrl);
            if (response.ok) return true;
        } catch {
            // Flask démarre parfois après le lancement de la fenêtre.
        }
        await new Promise((resolve) => setTimeout(resolve, 250));
    }
    return false;
}

async function createWindow() {
    const window = new BrowserWindow({
        width: 1440,
        height: 960,
        minWidth: 900,
        minHeight: 640,
        backgroundColor: "#0a0a0a",
        webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
        },
    });
    const ready = await waitForServer();
    if (!ready) {
        dialog.showErrorBox("DowFlow", "Le serveur Flask n’a pas démarré sur le port 5001.");
        app.quit();
        return;
    }
    await window.loadURL(serverUrl);
}

app.whenReady().then(() => {
    startFlask();
    createWindow();
});

app.on("window-all-closed", () => {
    if (serverProcess && !serverProcess.killed) serverProcess.kill();
    if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
    if (serverProcess && !serverProcess.killed) serverProcess.kill();
});
