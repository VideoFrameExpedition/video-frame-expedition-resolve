# Connecter le MCP et accéder à Video Frame Expedition depuis vos autres appareils

[English](connect-mcp.md) · **Français**

Video Frame Expedition for DaVinci Resolve contient un **serveur MCP** : un assistant (Claude
Code, Claude Desktop, Cursor, VS Code, Codex…) peut y consulter vos vidéos analysées. Il cherche
des plans (lieu, lumière, météo, sujets), lit ce qui est dit, regarde des images et prépare un
montage dans DaVinci Resolve. Le serveur répond tant que l'application tourne (`run.bat`) ; il ne
la démarre jamais.

La page **Connexions** de l'interface (menu de gauche) donne tout ce qui suit **pour votre
ordinateur** : adresses, jeton, et configurations prêtes à copier avec les bons chemins.

## Sur l'ordinateur de l'application

Adresse du serveur MCP : `http://127.0.0.1:8765/mcp` (transport HTTP « streamable »). Aucun jeton
n'est demandé sur cet ordinateur.

### Claude Code

```powershell
claude mcp add --scope user --transport http vfe-vision http://127.0.0.1:8765/mcp
```

Ou bien le plugin Claude Code, ci-dessous. Choisissez l'un ou l'autre : avec les deux, Claude
Code garde l'entrée ajoutée par `claude mcp add` et laisse de côté celle du plugin.

### Le plugin Claude Code

Un **plugin** est un paquet qu'on installe dans Claude Code. Celui de Video Frame Expedition,
`video-frame-expedition`, a son propre dépôt
([video-frame-expedition-claude-plugin](https://github.com/VideoFrameExpedition/video-frame-expedition-claude-plugin))
et contient deux choses :

- la **connexion au serveur MCP** de l'application (`vfe-vision`), la même que `claude mcp add` ;
- un **skill**, `resolve-editing` : la méthode de montage que Claude lit avant de travailler dans
  Resolve (relier la timeline ouverte aux analyses, choisir les plans, écrire une liste de
  montage, couper aux points sûrs, monter dans une nouvelle timeline, recadrer pour un autre
  format, poser des marqueurs), avec les pièges connus de l'API de scripts de DaVinci Resolve
  Studio 21.1 et des modèles de scripts testés.

Il demande Video Frame Expedition 1.1.1 ou plus récente, lancée. Pour monter : DaVinci Resolve
Studio 21.1 ou plus récent, avec la case « Outils Resolve pour l'assistant » (page Connexions)
ou le serveur de Resolve branché sur Claude Code (plus bas). Les petits scripts du skill
demandent Python 3 et Windows PowerShell.

Dans Claude Code, installez-le, puis confirmez ses réglages :

```text
/plugin marketplace add VideoFrameExpedition/video-frame-expedition-claude-plugin
/plugin install video-frame-expedition@video-frame-expedition
/plugin configure video-frame-expedition@video-frame-expedition
```

Le nom revient deux fois dans `video-frame-expedition@video-frame-expedition` : c'est le
plugin, puis le catalogue qui le propose.

| Réglage | Valeur |
|---|---|
| Adresse de l'application | `http://127.0.0.1:8765/mcp` (par défaut) quand l'application tourne sur cet ordinateur ; sinon son adresse sur votre réseau ou Tailscale, par exemple `http://100.64.12.34:8765/mcp` |
| Jeton d'accès | Vide sur l'ordinateur de l'application. Depuis un autre appareil : le jeton de la page Connexions (rangé dans le coffre de mots de passe du système) |

Si vous aviez déjà branché le serveur avec `claude mcp add`, retirez cette entrée pour que les
réglages du plugin s'appliquent :

```powershell
claude mcp remove --scope user vfe-vision
```

Ensuite, demandez avec vos mots : « Regarde la timeline ouverte dans Resolve et dis-moi ce que
contient chaque plan », « Monte 45 secondes avec les vidéos du lac, avec des coupes sûres »,
« Fais une version 9:16 de cette timeline, cadrée sur les personnes ». Claude prend le skill de
lui-même quand la demande porte sur un montage dans Resolve ; vous pouvez aussi l'appeler avec
`/video-frame-expedition:resolve-editing`.

Mettre à jour (puis relancer Claude Code), ou retirer le plugin, dans un terminal :

```powershell
claude plugin marketplace update video-frame-expedition
claude plugin update video-frame-expedition@video-frame-expedition

claude plugin uninstall video-frame-expedition@video-frame-expedition
```

Le plugin ne contacte que l'application, à l'adresse réglée, et n'a aucun hook : rien ne se
lance tout seul. Les scripts du skill ne tournent que lorsque Claude les appelle pour une tâche
que vous avez demandée, et ne téléchargent rien. Comme les outils de l'application, le skill ne
modifie jamais une timeline en place (il la duplique d'abord) et n'enregistre pas le projet.
Les autres assistants (Claude Desktop, Cursor, VS Code, Codex) se branchent comme ci-dessous,
sans le skill.

### Claude Desktop

Le fichier de configuration de Claude Desktop ne lance que des **commandes locales** (serveurs
« stdio »). Video Frame Expedition fournit donc un pont, `vfe mcp-stdio`, qui relaie les
messages vers l'application déjà lancée, sans jamais la démarrer : si elle ne tourne pas, Claude
Desktop reçoit une erreur claire (« Video Frame Expedition ne répond pas… lancez l'application
(run.bat) »).

Fichier à modifier :

- installation Microsoft Store (MSIX) :
  `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`
- installation depuis claude.ai : `%APPDATA%\Claude\claude_desktop_config.json`

Ajoutez l'entrée `vfe-vision` sous `mcpServers`, **en gardant le reste du fichier** (il contient
déjà vos préférences). Le programme est le Python de l'application, en chemin absolu : Claude
Desktop n'a alors besoin ni du `PATH` ni d'un dossier de travail particulier.

```json
{
  "mcpServers": {
    "vfe-vision": {
      "command": "C:\\…\\video-frame-expedition-resolve-windows\\backend\\.venv\\Scripts\\python.exe",
      "args": ["-m", "vfe_vision", "mcp-stdio", "--url", "http://127.0.0.1:8765/mcp"]
    }
  }
}
```

La page Connexions affiche ce bloc avec le chemin exact. Quittez ensuite **complètement** Claude
Desktop (icône près de l'horloge, « Quitter ») et relancez-le. Son journal du serveur se trouve
dans le dossier `logs` à côté du fichier de configuration (`mcp-server-vfe-vision.log`).

Les « connecteurs personnalisés » (URL distante) de Claude Desktop passent par les serveurs
d'Anthropic : ils ne peuvent pas joindre une adresse locale ni une adresse Tailscale. Utilisez le
pont ci-dessus.

### Cursor

`%USERPROFILE%\.cursor\mcp.json` (ou `.cursor/mcp.json` dans un projet) :

```json
{ "mcpServers": { "vfe-vision": { "url": "http://127.0.0.1:8765/mcp" } } }
```

### VS Code

Commande « MCP: Open User Configuration » (ou `.vscode/mcp.json` dans un projet) :

```json
{ "servers": { "vfe-vision": { "type": "http", "url": "http://127.0.0.1:8765/mcp" } } }
```

### OpenAI Codex CLI

`%USERPROFILE%\.codex\config.toml` :

```toml
[mcp_servers.vfe-vision]
url = 'http://127.0.0.1:8765/mcp'
```

Une version de Codex qui ne prend pas les serveurs HTTP passe par le pont stdio :

```toml
[mcp_servers.vfe-vision]
command = 'C:\…\backend\.venv\Scripts\python.exe'
args = ["-m", "vfe_vision", "mcp-stdio", "--url", "http://127.0.0.1:8765/mcp"]
```

### Autre client MCP

- HTTP « streamable » : `http://127.0.0.1:8765/mcp`.
- stdio : `"<python de l'application>" -m vfe_vision mcp-stdio --url http://127.0.0.1:8765/mcp`
  (options : `--token`, ou la variable `VFE_MCP_TOKEN`, pour une application sur un autre
  appareil). La sortie standard ne porte que le protocole ; le journal va sur la sortie d'erreur.

### DaVinci Resolve

Deux façons de laisser l'assistant agir dans Resolve, cumulables.

**Les outils Resolve de l'application.** Page **Connexions › Outils Resolve pour l'assistant**,
cochez « Outils DaVinci Resolve améliorés pour l'assistant » (désactivé par défaut). Quatre outils
MCP de Video Frame Expedition pilotent alors Resolve par les scripts figés de l'application :
`read_timeline` lit la timeline ouverte, `plan_reframe` prépare un recadrage,
`build_timeline` construit une timeline neuve « … - vfe vN », `apply_markers` pose les
marqueurs. L'assistant n'envoie que des données, jamais de code, et reçoit des résultats
courts : il consomme bien moins de tokens qu'en écrivant et en relisant ses propres scripts.
Aucune timeline existante n'est modifiée et le projet n'est pas enregistré (Ctrl+S dans Resolve
si vous gardez le montage).
Aucun autre serveur n'est à brancher.

**Le MCP de DaVinci Resolve Studio 21** (`C:\Program Files\Blackmagic Design\DaVinci
Resolve\ResolveMCP.exe`, en stdio), pour tout ce que ces quatre outils ne couvrent pas
(fondus enchaînés, effets) : l'assistant y écrit ses scripts, et Video Frame Expedition lui fournit
les plans (`find_clips` : chemins, points d'entrée et de sortie, timecodes). Branchez les deux au
même assistant :

```powershell
claude mcp add --scope user davinci-resolve -- "C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolveMCP.exe"
```

Pour Claude Desktop, ajoutez sous `mcpServers` :

```json
"davinci-resolve": {
  "command": "C:\\Program Files\\Blackmagic Design\\DaVinci Resolve\\ResolveMCP.exe"
}
```

### DaVinci Resolve sur un autre ordinateur (un Mac, par exemple)

Les **outils Resolve de l'application** fonctionnent tels quels : ils passent par ce PC, qui lit
et pilote le Resolve du Mac (réglages ci-dessous). Claude peut alors rester sur ce PC.

Le **MCP de Resolve**, lui, ne pilote que le Resolve de sa propre machine : pour s'en servir,
Claude (Claude Code ou Claude Desktop) s'installe **sur l'ordinateur de Resolve**, et y branche
les deux MCP :

- celui de Resolve, en local (sur Mac, l'extension `DaVinciResolve.mcpb` fournie avec Resolve, ou
  son programme `ResolveMCP`) ;
- celui de Video Frame Expedition, par Tailscale avec le jeton (voir plus bas « Les assistants
  sur vos autres appareils »).

Dans Video Frame Expedition, page **Connexions › Où tourne DaVinci Resolve** : choisissez « sur
un autre ordinateur », donnez son nom ou son adresse Tailscale, puis les **mêmes dossiers vus des
deux côtés** (par exemple `D:\cats 2026` ici et `/Volumes/cats 2026` sur le Mac, si le Mac lit
les rushs par un partage réseau de ce PC). « Tester la connexion » lit le projet ouvert dans
Resolve.

Sur le Mac, dans Resolve Studio : **Préférences › Système › Général › Script externe : Réseau**,
et le pare-feu du Mac laisse entrer ce PC : le serveur de scripts de Resolve (port 1144) **et**
DaVinci Resolve lui-même, qui choisit un autre port à chaque lancement. Si l'ordinateur de
Resolve est un PC Windows, les règles que Resolve installe dans le pare-feu ne valent que pour un
réseau « privé » : sur un réseau classé « public », autorisez `fuscript.exe` et `Resolve.exe`
(dossier de DaVinci Resolve) pour ce réseau. Ce PC garde DaVinci Resolve installé (sans le
lancer) : l'application utilise sa bibliothèque de scripts pour lire le Resolve du Mac.

Pour les sous-titres, le partage des rushs doit être **en lecture et écriture** : l'application
écrit les pistes de la timeline à côté de sa première vidéo (`<timeline>_TIMELINE_FR.srt`), où
Resolve les lit pour les poser, et les sous-titres de chaque vidéo à côté d'elle. Sur un partage
en lecture seule, la timeline arrive sans ses pistes de sous-titres, et l'application le dit.

## Accès via Tailscale

[Tailscale](https://tailscale.com) relie vos appareils dans un réseau privé chiffré (le
« tailnet »). Video Frame Expedition peut y écouter pour que votre portable, votre tablette ou
votre téléphone ouvrent l'interface et que leurs assistants utilisent le MCP.

### Activer

1. Créez (ou complétez) le fichier `.env` à la racine du dépôt, à côté de `run.bat` :

   ```ini
   VFE_TAILSCALE=true
   ```

   Pour un seul lancement : `run.bat tailscale`. On peut aussi donner des adresses précises :
   `VFE_EXTRA_HOSTS=100.64.12.34` (adresses IP uniquement, séparées par des virgules).

2. Autorisez le port dans le pare-feu Windows, **une fois**, dans un PowerShell ouvert **en tant
   qu'administrateur** (le tailnet est classé « Privé » par Windows) :

   ```powershell
   New-NetFirewallRule -DisplayName "Video Frame Expedition (Tailscale)" -Direction Inbound -Protocol TCP -LocalPort 8765 -RemoteAddress 100.64.0.0/10 -Profile Private -Action Allow
   ```

   Vérifiez le profil avec `Get-NetConnectionProfile -InterfaceAlias Tailscale` : s'il est
   « Public », remplacez `-Profile Private` par `-Profile Any`. Si Windows a un jour proposé
   d'autoriser Python et que vous avez refusé, une règle **de blocage** pour `python.exe` existe
   peut-être (elle l'emporte sur toute autorisation) : supprimez-la dans « Pare-feu Windows
   Defender avec fonctions avancées de sécurité » → « Règles de trafic entrant ».

3. Relancez l'application. La console affiche les adresses joignables depuis vos appareils, par
   exemple `http://100.64.12.34:8765` et `http://<machine>.<tailnet>.ts.net:8765` (nom MagicDNS).

L'application écoute alors sur `127.0.0.1` **et** sur l'adresse Tailscale de l'ordinateur, chacune
avec sa propre socket : jamais sur `0.0.0.0`, donc rien n'est exposé sur le réseau local (box,
Wi-Fi). Si Tailscale est arrêté au démarrage, l'application démarre quand même, sur cet
ordinateur seulement, avec un avertissement (console et page Connexions) ; relancez-la une fois
Tailscale connecté.

### Le jeton

Depuis un autre appareil, un **jeton d'accès** est demandé. Il est créé au premier démarrage avec
l'accès à distance, dans le dossier de données (`%LOCALAPPDATA%\vfe-vision\api-token.txt`,
lisible par votre compte Windows seulement), à moins que `VFE_API_TOKEN` ne soit défini.

- `vfe token` l'affiche (le jeton seul sur la sortie standard) ; en entier, depuis le dossier
  de l'application : `uv run --frozen --no-dev --project backend python -m vfe_vision token` ;
- `vfe token --rotate` en crée un nouveau : redémarrez l'application, puis reconnectez vos
  appareils (toutes les sessions tombent) ;
- la page Connexions l'affiche (masqué, bouton « Afficher ») **seulement** quand elle est ouverte
  sur l'ordinateur de l'application.

Dans un navigateur distant, une page de connexion demande le jeton, puis ouvre une session de
30 jours (cookie `HttpOnly`, `SameSite=Strict`) ; « Se déconnecter » est en haut à droite. Après
10 mauvais jetons en 5 minutes, l'appareil attend une minute.

### Les assistants sur vos autres appareils

Remplacez `127.0.0.1` par l'adresse Tailscale et ajoutez le jeton :

```powershell
claude mcp add --scope user --transport http vfe-vision http://100.64.12.34:8765/mcp --header "Authorization: Bearer <JETON>"
```

- Cursor : `"url": "http://100.64.12.34:8765/mcp", "headers": { "Authorization": "Bearer <JETON>" }` ;
- VS Code : `"headers": { "Authorization": "Bearer ${input:vfe-token}" }` avec une entrée
  `inputs` de type `promptString` (`"password": true`) : VS Code demande le jeton une fois ;
- Codex : `url = 'http://100.64.12.34:8765/mcp'` et `bearer_token_env_var = "VFE_VISION_TOKEN"`
  (variable d'environnement contenant le jeton) ;
- Claude Desktop sur un autre ordinateur : un pont stdio vers HTTP, par exemple `vfe mcp-stdio
  --url http://100.64.12.34:8765/mcp` avec la variable `VFE_MCP_TOKEN` (il faut alors le code
  de Video Frame Expedition sur cet ordinateur), ou tout pont capable d'envoyer un en-tête
  `Authorization`.

Le MCP accepte seulement l'en-tête `Authorization: Bearer` depuis un autre appareil (pas le
cookie du navigateur). Sur l'ordinateur de l'application, rien ne change : Claude Code reste
enregistré sur `http://127.0.0.1:8765/mcp`.

### HTTPS avec `tailscale serve` (option)

`tailscale serve --bg 8765` publie l'application en HTTPS sur
`https://<machine>.<tailnet>.ts.net`, dans le tailnet seulement (certificat fourni par
Tailscale), en relayant les requêtes vers `127.0.0.1:8765`. Video Frame Expedition ne tient pour
locale qu'une requête arrivée sur `127.0.0.1` **avec** l'hôte `127.0.0.1` ou `localhost` : une requête
relayée qui porte le nom de la machine demande donc le jeton, comme l'accès direct (gardez
`VFE_TAILSCALE=true` pour que ce nom soit reconnu). N'utilisez **jamais** `tailscale funnel`, qui
publierait l'application sur internet.

### Confidentialité

Le tailnet est privé : le trafic va directement d'un de vos appareils à l'autre, chiffré de bout
en bout par WireGuard, et vos vidéos, images et analyses ne quittent jamais vos appareils. Seuls les appareils de votre tailnet peuvent joindre l'adresse, et ils doivent en
plus présenter le jeton. N'ajoutez au tailnet que des appareils de confiance, et pas de
« partage de machine » avec d'autres comptes.

## En cas de souci

| Symptôme | Cause probable |
|---|---|
| « Video Frame Expedition ne répond pas à … » | l'application n'est pas lancée (`run.bat`) |
| La page ne s'ouvre pas depuis le téléphone | règle de pare-feu absente, Tailscale déconnecté sur l'un des deux appareils, ou application lancée avant Tailscale |
| « Jeton d'API requis » / 401 | jeton absent ou changé (`vfe token`) |
| « Trop d'essais » / 429 | 10 mauvais jetons : attendre une minute |
| 400 « Invalid host header » | adresse non reconnue : utilisez celles de la page Connexions |
| Claude Desktop ne voit pas l'outil | JSON invalide, ou Claude Desktop pas complètement quitté |
| Le plugin Claude Code ne prend pas son adresse ou son jeton | une entrée `vfe-vision` ajoutée par `claude mcp add` l'emporte : `claude mcp remove --scope user vfe-vision` |

Sécurité : [SECURITY.fr.md](../../SECURITY.fr.md).
