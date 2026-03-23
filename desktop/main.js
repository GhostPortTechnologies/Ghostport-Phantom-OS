const { app, BrowserWindow, Menu, Tray, shell, dialog } = require("electron");
const path = require("path");
const net = require("net");

// Default connection target — user can change in settings
let ghostportUrl = "http://192.168.50.1:4200";
const SETTINGS_FILE = path.join(app.getPath("userData"), "settings.json");

let mainWindow = null;
let tray = null;

function loadSettings() {
  try {
    const fs = require("fs");
    const data = JSON.parse(fs.readFileSync(SETTINGS_FILE, "utf8"));
    if (data.url) ghostportUrl = data.url;
  } catch {}
}

function saveSettings() {
  const fs = require("fs");
  fs.writeFileSync(SETTINGS_FILE, JSON.stringify({ url: ghostportUrl }, null, 2));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 800,
    minWidth: 380,
    minHeight: 600,
    title: "GhostPort",
    icon: path.join(__dirname, "icons", "icon.png"),
    backgroundColor: "#060A06",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
    autoHideMenuBar: true,
  });

  // Custom menu
  const menu = Menu.buildFromTemplate([
    {
      label: "GhostPort",
      submenu: [
        {
          label: "Change Device IP...",
          click: async () => {
            const { response, checkboxChecked } = await dialog.showMessageBox(mainWindow, {
              type: "question",
              title: "Device Connection",
              message: `Currently connected to:\n${ghostportUrl}\n\nEnter new GhostPort IP in the next dialog.`,
              buttons: ["Change IP", "Cancel"],
            });
            if (response === 0) {
              const input = await promptForIp();
              if (input) {
                ghostportUrl = input.startsWith("http") ? input : `http://${input}:4200`;
                saveSettings();
                mainWindow.loadURL(ghostportUrl);
              }
            }
          },
        },
        { type: "separator" },
        { label: "Reload", accelerator: "CmdOrCtrl+R", click: () => mainWindow.reload() },
        { type: "separator" },
        { role: "quit" },
      ],
    },
  ]);
  Menu.setApplicationMenu(menu);

  // Load the GhostPort UI
  mainWindow.loadURL(ghostportUrl);

  // Handle connection failures gracefully
  mainWindow.webContents.on("did-fail-load", (event, errorCode, errorDescription) => {
    mainWindow.loadFile(path.join(__dirname, "offline.html"));
  });

  // Open external links in default browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.on("closed", () => { mainWindow = null; });
}

async function promptForIp() {
  // Simple prompt using a small BrowserWindow
  return new Promise((resolve) => {
    const prompt = new BrowserWindow({
      width: 400, height: 180,
      parent: mainWindow, modal: true,
      resizable: false,
      webPreferences: { nodeIntegration: true, contextIsolation: false },
    });
    prompt.setMenu(null);
    const html = `<html><body style="background:#060A06;color:#39ff8f;font-family:monospace;padding:20px">
      <div style="font-size:12px;margin-bottom:12px">Enter GhostPort device IP:</div>
      <input id="ip" type="text" value="${ghostportUrl.replace('http://','').replace(':4200','')}"
        style="width:100%;padding:8px;background:#0a0a0a;border:1px solid #333;color:#39ff8f;font-family:monospace;font-size:14px"
        autofocus />
      <div style="margin-top:12px;display:flex;gap:8px">
        <button onclick="require('electron').ipcRenderer.send('ip-result',document.getElementById('ip').value)"
          style="flex:1;padding:8px;background:#0d2a1a;border:1px solid #39ff8f;color:#39ff8f;font-family:monospace;cursor:pointer">CONNECT</button>
        <button onclick="require('electron').ipcRenderer.send('ip-result',null)"
          style="padding:8px;background:#1a0a0a;border:1px solid #ff4444;color:#ff4444;font-family:monospace;cursor:pointer">CANCEL</button>
      </div>
      <script>document.getElementById('ip').addEventListener('keydown',e=>{if(e.key==='Enter')require('electron').ipcRenderer.send('ip-result',document.getElementById('ip').value)})</script>
    </body></html>`;
    prompt.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(html));
    require("electron").ipcMain.once("ip-result", (e, val) => {
      prompt.close();
      resolve(val);
    });
    prompt.on("closed", () => resolve(null));
  });
}

app.whenReady().then(() => {
  loadSettings();
  createWindow();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("activate", () => {
  if (mainWindow === null) createWindow();
});
