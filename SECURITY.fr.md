# Sécurité

[English](SECURITY.md) · **Français**

Video Frame Expedition for DaVinci Resolve est une application **locale** : le serveur écoute
sur `127.0.0.1` par défaut. Il peut aussi écouter sur l'adresse Tailscale de l'ordinateur
(`VFE_TAILSCALE=true`) ; il n'écoute jamais de lui-même sur `0.0.0.0`.

## Modèle de menace retenu

- **Pages web malveillantes** qui tenteraient d'appeler l'API locale (CSRF, DNS rebinding) :
  vérification de l'en-tête `Origin`, en-tête personnalisé `X-VFE-Client` obligatoire sur toute
  écriture, liste blanche d'hôtes (`TrustedHost`). Aucune page de l'application ne peut
  s'afficher dans le cadre d'un autre site (`X-Frame-Options`, `frame-ancestors`). Un chemin de
  fichier pris dans une adresse est vérifié sur son texte avant tout accès au disque : un chemin
  réseau (`\\hôte\partage`) n'est jamais ouvert, et Windows n'envoie donc jamais les
  identifiants de l'utilisateur à une machine désignée par une page web.
- **Contenu non fiable dans les vidéos** (transcriptions, texte à l'écran) qui pourrait contenir
  des instructions destinées à un LLM : ce contenu est toujours encadré comme « non fiable » dans
  les prompts et les réponses MCP, et le serveur MCP ne peut pas ajouter de dossier à la
  bibliothèque par défaut.
- **Accès aux fichiers** : l'API ne lit que les fichiers situés sous les racines de bibliothèque
  déclarées. Elle n'y écrit que le fichier d'analyse de chaque vidéo, un par langue
  (`<nom>_FR.txt`, `<nom>_EN.txt` ; désactivable) et, quand vous créez une timeline
  avec des sous-titres, ses fichiers `.srt` : écriture atomique, jamais à la place
  d'un fichier qu'elle n'a pas écrit, jamais de dossier créé. À la relecture, un fichier
  d'analyse n'est repris que s'il porte le format de l'application et l'empreinte et la taille
  de la vidéo.
- **Exécution de code** : aucun appel `shell=True` ; aucun code n'est généré à partir de sorties
  d'IA. Le script DaVinci Resolve des marqueurs est figé et ne lit qu'un fichier JSON ; les
  processus enfants qui parlent à Resolve n'exécutent que leurs commandes fixes : un assistant
  ne leur envoie que des données.
- **Écriture dans DaVinci Resolve** : l'application ajoute au projet ouvert, elle ne remplace rien
  et ne l'enregistre jamais. « Créer une timeline » agit à la demande de l'utilisateur. Les outils
  Resolve de l'assistant sont **désactivés par défaut** (page Connexions) : ils créent des timelines
  neuves « … - vfe vN » et posent marqueurs et métadonnées sur les clips du media pool, en ne
  remplaçant que ceux qu'ils ont posés. `build_timeline` accepte aussi un chemin de fichier tel que
  Resolve le voit : Resolve importe alors ce fichier dans le chutier « Video Frame Expedition »,
  sans que l'application le lise ni vérifie qu'il se trouve sous une racine de la bibliothèque.
- **Chargement de modèles dans LM Studio** : la page « Banc d'essai » demande à LM
  Studio, par son API locale, de charger et de décharger les modèles de vision que l'utilisateur
  a cochés, parmi ceux que LM Studio possède déjà ; rien n'est téléchargé. Cette requête est une
  écriture comme les autres (`Origin` vérifiée, en-tête `X-VFE-Client`, jeton à distance). Aucune
  analyse et aucun outil MCP ne charge de modèle.
- **LM Studio sur un autre ordinateur** : par défaut, les images des vidéos ne
  quittent pas l'ordinateur (`http://127.0.0.1:1234`). L'utilisateur peut désigner un autre LM
  Studio dans la page Système : les images et les textes des analyses lui sont alors envoyés, en
  clair sur un réseau local (http), chiffrés sur Tailscale. La carte le dit avant
  l'enregistrement et le diagnostic l'affiche en avertissement. L'adresse acceptée est un hôte
  et un port, sans chemin ni identifiants ; le test de connexion ne fait qu'un
  `GET /api/v1/models` et n'en rend que des compteurs. Le jeton d'API d'un LM Studio n'est
  envoyé qu'à son adresse, n'est jamais rendu par l'API, et se trouve en clair dans la base
  locale. Aucun outil MCP ne change cette adresse.
- **Extension compilée refusée par Windows** : quand Smart App Control refuse une
  extension d'un paquet Python qui existe aussi en Python pur, l'application renomme ce fichier
  dans son propre environnement (`….pyd.refused`) et se relance. Elle n'exécute jamais un
  fichier refusé et ne modifie aucun réglage de Windows.
- **Fichiers compilés vérifiés** : à la fin de l'installation, et dans la page Système,
  l'application demande à Windows s'il refuse chacun des fichiers compilés (`.pyd`, `.dll`)
  des paquets dont elle se sert et de Python lui-même. Pour cela, Windows projette le fichier
  comme il le ferait pour l'exécuter, sans en exécuter le code ni charger ce dont il dépend
  (`DONT_RESOLVE_DLL_REFERENCES`). Un refus est nommé (fichier et paquet) ; l'application ne
  le contourne pas.
- **Appels réseau** : une fois l'application installée, les analyses n'en font que deux. Nominatim
  (OpenStreetMap) reçoit une position arrondie à trois décimales (à 70 mètres près environ), pour
  nommer le lieu ; Open-Meteo reçoit une position arrondie à deux décimales (à 700 mètres près
  environ) et une date, pour la météo. Un seul interrupteur de la page Système coupe les deux, et
  masque aussi la carte de l'onglet Contexte, dont les tuiles viennent d'OpenStreetMap. La page
  d'aide charge ses polices depuis Google Fonts et ses vidéos de présentation depuis
  youtube-nocookie.com, seulement quand le lecteur arrive à leur hauteur. Les modèles sont
  téléchargés à l'installation, ou quand vous en demandez un (Hugging Face, PyPI, GitHub, GeoNames).
  L'application n'envoie aucune télémétrie.
- **Processus enfants** : le worker d'analyse, le moteur de transcription et les
  scripts qui parlent à DaVinci Resolve sont liés à l'application (Job Object sous Windows ;
  groupe de processus et fil de vie sur un Mac) : si elle disparaît, ils s'arrêtent avec elle, et
  rien d'eux ne continue en arrière-plan. Sur un Mac, l'application lance aussi `osascript` (la
  fenêtre de choix d'un dossier), `diskutil`, `sysctl` et `pgrep`, en lecture seulement.
- **Installation** : `install.bat`, `install.command` et la ligne d'installation du Mac
  (`install.sh`) installent ce qui manque par winget ou par Homebrew, qui demandent eux-mêmes
  l'accord de l'administrateur quand il le faut, puis téléchargent les modèles. Sous Windows,
  quand Smart App Control est actif, Node.js vient de son ZIP officiel (nodejs.org), et ExifTool
  du sien (exiftool.org) quand winget n'a pas pu l'installer : chacun est vérifié par son
  empreinte SHA-256 publiée et installé pour l'utilisateur seul. Quand Smart App Control est
  actif ou en évaluation, Python 3.12 vient de python.org (signé, par winget, pour
  l'utilisateur) plutôt que du téléchargement d'uv, qui n'est pas signé.
  `install.bat`
  retire la marque du Web des fichiers du dossier de l'application, `install.command` leur
  marque de quarantaine, et rien en dehors de ce dossier. Sur un Mac, l'installation ajoute
  aussi « Video Frame Expedition » au dossier Applications de l'utilisateur : une petite
  application faite sur place, signée sur place (signature ad hoc), qui ouvre `run.command` ;
  sous Windows, un raccourci « Video Frame Expedition » vers `run.bat`, dans le menu Démarrer de
  l'utilisateur. La ligne du Mac exécute un script téléchargé sur GitHub : on peut le lire
  avant (`install.sh`, à la racine du dépôt).
- **Mise à jour** : `update.bat` et `update.command` ne téléchargent que depuis le dépôt de
  l'application sur GitHub, en HTTPS : l'adresse de sa dernière version publiée, puis le ZIP de
  celle-ci (un dossier venu de Git se met à jour par `git pull`). Ils remplacent les fichiers du
  dossier de l'application et retirent, de ses dossiers propres (`backend`, `frontend`,
  `scripts`, `docs`), ceux que la nouvelle version n'a plus ; jamais le dossier de données, le
  fichier `.env`, l'environnement de l'application ni rien d'autre du dossier. Ils lancent
  ensuite l'installation de la nouvelle version. L'installation met « Video Frame Expedition -
  mise à jour » à côté de l'application (menu Démarrer, dossier Applications) : il les ouvre.
- **Export, import et réinitialisation des données** (page Système) : un export est préparé par
  une requête d'écriture (en-tête `X-VFE-Client`, voir plus haut), puis téléchargé une seule
  fois par un lien impossible à deviner ; il contient les réglages, jetons enregistrés compris.
  Un import ne garde d'une archive que la base de données et les images extraites, refuse tout
  chemin qui sortirait du dossier de données, et n'accepte qu'une base saine de l'application,
  d'une version qu'il connaît. L'import et la réinitialisation se font au démarrage suivant,
  avant que quoi que ce soit n'ouvre la base, une fois une copie de l'actuelle dans le dossier
  `backups` ; le redémarrage demandé depuis l'interface arrête l'application comme un Ctrl+C, et
  son lanceur (`run.bat`, `run.command`) la relance.

## Accès depuis d'autres appareils

- **Local** veut dire : arrivée sur une adresse de boucle **et** hôte de boucle (`127.0.0.1`,
  `localhost`, `::1`). Ces requêtes n'ont jamais besoin de jeton. Une requête relayée par un
  proxy de l'ordinateur (hôte différent) est traitée comme distante.
- **Toute requête distante** présente le jeton : en-tête `Authorization: Bearer`, paramètre
  `access_token` (médias, SSE), ou cookie de session de la page de connexion. `/mcp` n'accepte
  que l'en-tête `Authorization`.
- **Jeton** : `VFE_API_TOKEN`, sinon généré (256 bits) au premier démarrage avec l'accès à
  distance dans `api-token.txt` du dossier de données, lisible par l'utilisateur seulement ;
  `vfe token` l'affiche, `vfe token --rotate` le remplace (`vfe` tel que défini sous « Ligne de
  commande » dans le [README](README.fr.md#installation)). La page « Connexions » ne le montre
  qu'à un navigateur ouvert sur l'ordinateur de l'application. Si `VFE_HOST` n'est pas une
  adresse de boucle, `VFE_API_TOKEN` reste obligatoire.
- **Mauvais jetons** : comparaison à temps constant, 0,5 s d'attente par échec, adresse bloquée
  une minute après 10 échecs en 5 minutes.
- **Session** : cookie `HttpOnly`, `SameSite=Strict` (`Secure` en HTTPS), 30 jours, signé avec
  le jeton (changer le jeton ferme toutes les sessions). Avec ce cookie, toute écriture exige une
  `Origin` autorisée en plus de `X-VFE-Client`.
- **Listes blanches** d'hôtes et d'origines étendues à l'adresse Tailscale écoutée et aux noms
  MagicDNS de l'ordinateur seulement.
- Le trafic du tailnet est chiffré (WireGuard) et reste entre les appareils de l'utilisateur ;
  n'utilisez jamais `tailscale funnel` (publication sur internet).

## Signaler un problème

Signalez-le de façon privée, par l'onglet « Security » du dépôt GitHub (« Report a
vulnerability ») : n'ouvrez pas d'issue publique.
