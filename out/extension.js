"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const path = __importStar(require("path"));
const fs = __importStar(require("fs"));
const child_process_1 = require("child_process");
const node_fetch_1 = __importDefault(require("node-fetch"));
let pythonProcess = null;
const SERVER_URL = 'http://localhost:5001';
function activate(context) {
    console.log('LyricSync is active!');
    // Start Python Server
    startPythonServer(context);
    // Register Webview Command
    const disposable = vscode.commands.registerCommand('lyricsync.showLyrics', () => {
        createLyricSyncWebview(context);
    });
    // Register Debug Command (updated to use local Python server)
    const debugCommand = vscode.commands.registerCommand('lyricsync.debugSearch', async () => {
        const songTitle = await vscode.window.showInputBox({ prompt: 'Enter song title to search (Backend)' });
        if (songTitle) {
            vscode.window.showInformationMessage(`Searching for: ${songTitle}...`);
            try {
                const response = await (0, node_fetch_1.default)(`${SERVER_URL}/search?q=${encodeURIComponent(songTitle)}`);
                const data = await response.json();
                if (response.ok) {
                    vscode.window.showInformationMessage(`Found: ${data.title} by ${data.artist}`);
                    console.log(data);
                }
                else {
                    vscode.window.showErrorMessage(`Error: ${data.error}`);
                }
            }
            catch (error) {
                vscode.window.showErrorMessage(`Connection failed: ${error}`);
            }
        }
    });
    context.subscriptions.push(disposable, debugCommand);
}
function startPythonServer(context) {
    const pythonPath = 'python';
    const scriptPath = path.join(context.extensionPath, 'backend', 'server.py');
    if (!fs.existsSync(scriptPath)) {
        vscode.window.showErrorMessage(`Backend not found at: ${scriptPath}`);
        return;
    }
    console.log(`Starting Python server: ${scriptPath}`);
    pythonProcess = (0, child_process_1.spawn)(pythonPath, [scriptPath]);
    pythonProcess.stdout?.on('data', (data) => {
        console.log(`[Python]: ${data}`);
    });
    pythonProcess.stderr?.on('data', (data) => {
        console.error(`[Python Err]: ${data}`);
    });
}
function createLyricSyncWebview(context) {
    const panel = vscode.window.createWebviewPanel('lyricSync', 'LyricSync', vscode.ViewColumn.Beside, {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.file(path.join(context.extensionPath, 'dist'))]
    });
    const distPath = path.join(context.extensionPath, 'dist');
    const indexPath = path.join(distPath, 'index.html');
    if (fs.existsSync(indexPath)) {
        let htmlContent = fs.readFileSync(indexPath, 'utf8');
        // Update paths for assets
        htmlContent = htmlContent.replace(/(href|src)=["']([^"']*?\.(js|css|ico|png|jpg|jpeg|svg))["']/g, (match, type, assetPath) => {
            const normalizedPath = assetPath.replace(/^(\.?\/)/, '');
            const assetUri = panel.webview.asWebviewUri(vscode.Uri.file(path.join(distPath, normalizedPath)));
            return `${type}="${assetUri}"`;
        });
        // Remove importmap
        htmlContent = htmlContent.replace(/<script type="importmap">[\s\S]*?<\/script>/, '');
        // Inject CSP
        const csp = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src http://localhost:5001; style-src ${panel.webview.cspSource} 'unsafe-inline' https:; script-src ${panel.webview.cspSource} 'unsafe-inline' https:; img-src ${panel.webview.cspSource} https: data:; font-src ${panel.webview.cspSource} https:; connect-src http://localhost:5001 https:;">`;
        htmlContent = htmlContent.replace('<head>', `<head>${csp}`);
        panel.webview.html = htmlContent;
    }
    else {
        panel.webview.html = `<html><body><h1>File not found</h1><p>${indexPath}</p></body></html>`;
    }
    // Backend communication
    panel.webview.onDidReceiveMessage(async (message) => {
        if (message.command === 'getLyrics') {
            try {
                const response = await (0, node_fetch_1.default)(`${SERVER_URL}/search?q=${encodeURIComponent(message.songTitle)}`);
                const data = await response.json();
                panel.webview.postMessage({ command: 'lyricsResult', data: data });
            }
            catch (error) {
                console.error(error);
                panel.webview.postMessage({ command: 'error', error: 'Connection to music server failed.' });
            }
        }
    });
    panel.onDidDispose(() => {
        // Cleanup if needed
    });
}
function deactivate() {
    if (pythonProcess) {
        pythonProcess.kill();
        pythonProcess = null;
    }
}
//# sourceMappingURL=extension.js.map