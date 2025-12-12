import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';

export function activate(context: vscode.ExtensionContext) {
	console.log('LyricSync is active!');

	const disposable = vscode.commands.registerCommand('lyricsync.showLyrics', () => {
		createLyricSyncWebview(context);
	});

	const debugCommand = vscode.commands.registerCommand('lyricsync.debugSearch', async () => {
		const songTitle = await vscode.window.showInputBox({ prompt: 'Enter song title to search' });
		if (songTitle) {
			vscode.window.showInformationMessage(`Searching for: ${songTitle}...`);
			const lyrics = await fetchLyricsFromExternalAPI(songTitle);
			if (lyrics === 'No lyrics found.' || lyrics === 'Failed to fetch lyrics.') {
				vscode.window.showErrorMessage(`Result: ${lyrics}`);
			} else {
				vscode.window.showInformationMessage('Lyrics found! (Check console for full text)');
				console.log(lyrics);
			}
		}
	});

	context.subscriptions.push(disposable, debugCommand);
}

function createLyricSyncWebview(context: vscode.ExtensionContext) {
	const panel = vscode.window.createWebviewPanel(
		'lyricSync',
		'LyricSync',
		vscode.ViewColumn.Beside,
		{
			enableScripts: true,
			localResourceRoots: [vscode.Uri.file(path.join(context.extensionPath, 'dist'))]
		}
	);

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

		// Remove the importmap which causes issues in VS Code webviews since the bundle is self-contained
		htmlContent = htmlContent.replace(/<script type="importmap">[\s\S]*?<\/script>/, '');

		// Inject CSP
		const csp = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${panel.webview.cspSource} 'unsafe-inline' https:; script-src ${panel.webview.cspSource} 'unsafe-inline' https:; img-src ${panel.webview.cspSource} https: data:; font-src ${panel.webview.cspSource} https:;">`;
		htmlContent = htmlContent.replace('<head>', `<head>${csp}`);

		// Inject debug script
		htmlContent = htmlContent.replace('</body>', `<script>console.log('Webview loaded'); window.addEventListener('error', (e) => console.error('Webview error:', e.message));</script></body>`);

		panel.webview.html = htmlContent;
	} else {
		panel.webview.html = `<html><body><h1>File not found</h1><p>Check if ${indexPath} exists.</p></body></html>`;
	}

	// Backend communication
	panel.webview.onDidReceiveMessage(async (message) => {
		console.log('Received message:', message);
		if (message.command === 'getLyrics') {
			try {
				const lyrics = await fetchLyricsFromExternalAPI(message.songTitle);
				panel.webview.postMessage({ command: 'lyricsResult', data: lyrics });
			} catch (error) {
				console.error('Error fetching lyrics:', error);
				panel.webview.postMessage({ command: 'lyricsResult', data: 'Error fetching lyrics.' });
			}
		}
	});
}

// Re-implementation of backend logic (Python server.py refactored to TS)
async function fetchLyricsFromExternalAPI(query: string): Promise<string> {
	try {
		// Using LrcLib as a generic provider example.
		// In a real scenario, this would match the logic from the user's server.py
		const response = await fetch(`https://lrclib.net/api/search?q=${encodeURIComponent(query)}`);
		if (!response.ok) {
			throw new Error(`API Error: ${response.statusText}`);
		}

		const data = await response.json() as any[];
		if (data && data.length > 0) {
			// Prefer synced lyrics, then plain.
			// Also return the track info for clarity
			const track = data[0];
			const meta = `[Info: Found "${track.trackName}" by "${track.artistName}"]\n\n`;
			return meta + (track.syncedLyrics || track.plainLyrics || 'No lyrics found.');
		}

		return 'No lyrics found.';
	} catch (error) {
		console.error(error);
		return 'Failed to fetch lyrics.';
	}
}

export function deactivate() { }
