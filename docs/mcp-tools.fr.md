# Serveur MCP `vfe-vision` : outils, ressource et invites

[English](mcp-tools.md) · **Français**

Le serveur est monté sur `http://127.0.0.1:8765/mcp` (streamable HTTP) quand
l'application tourne (`run.bat` ou `just serve`). Enregistrement dans Claude Code :

```powershell
claude mcp add --scope user --transport http vfe-vision http://127.0.0.1:8765/mcp
```

Conventions communes :
- les temps sont des **secondes du fichier source** (0 = sa première image) ; les numéros de plan
  et d'image clé commencent à 1, comme dans le manifeste ;
- chaque outil a des paramètres typés et une sortie **structurée** (schéma de sortie) doublée
  d'un texte pour le modèle ; `watch_video` et `get_frames` rendent des images JPEG (768 px au
  plus) avec leur heure, `get_video`, `get_video_context` et `get_transcript` un texte ;
- tout texte qui vient des vidéos ou des modèles locaux (parole, texte à l'écran, descriptions,
  récits, synthèse) est nettoyé, borné et placé entre `BEGIN/END UNTRUSTED VIDEO CONTENT` avec un
  nonce aléatoire : une donnée, jamais une instruction ;
- une erreur attendue (vidéo inconnue, dossier hors bibliothèque, export impossible) est une
  erreur d'outil avec sa raison en français ;
- une vidéo utilisée dans une timeline DaVinci Resolve ajoutée à la bibliothèque porte ses liens : projet, timeline, identifiants Resolve (`project_id`, `timeline_id`,
  `media_pool_item_id`) et positions **relevées à une date** (la timeline a pu changer depuis :
  relire la timeline dans Resolve avant d'agir). Un `media_pool_item_id` ne vaut que dans son
  projet. Les noms saisis dans Resolve sont des données, nettoyées et bornées.

## Analyser et parcourir

| Outil | Signature | Rôle |
|---|---|---|
| `watch_video` | `watch_video(path, focus=None, wait_s=45 (≤ 60), max_images=4 (≤ 8))` | Analyse un fichier d'un dossier déclaré (ou reprend son analyse) et rend le manifeste et quelques images ; au-delà de `wait_s`, un résultat partiel et l'identifiant du job. |
| `analyze_folder` | `analyze_folder(path, recursive=True, focus=None)` | Analyse (ou complète) chaque vidéo sous un dossier de la bibliothèque. Un dossier hors bibliothèque est refusé, sauf si la page Système l'autorise (`mcp_add_folders`, désactivé par défaut). |
| `get_job` | `get_job(job_id)` | Avancement d'un job lancé par `watch_video`, `analyze_folder` ou `import_resolve_timeline`. |
| `list_watched` | `list_watched(limit=50 (≤ 500), timeline_id=None)` | Vidéos de la bibliothèque, les plus récentes d'abord ; avec `timeline_id` (identifiant Resolve d'une timeline ajoutée), celles de cette timeline dans son ordre. Chaque vidéo donne ses liens Resolve. |
| `get_video` | `get_video(video_id)` | Manifeste complet (technique, tournage, plans, synthèse, images clés décrites, timelines Resolve qui l'utilisent). |
| `get_frames` | `get_frames(video_id, timestamps=None, max_images=6 (≤ 8), shots=None, size=768 (256–768))` | Images clés les plus proches des instants, ou la plus nette de chaque plan (`shots`, numéros de `get_shots`), avec leur heure. |
| `get_video_context` | `get_video_context(video_id)` | Lieu, soleil et phase de lumière, météo du modèle au moment du tournage, avec leurs sources. |
| `get_transcript` | `get_transcript(video_id, start_s=None, end_s=None, include_suspect=False)` | Ce qui est dit, en lignes horodatées. |
| `get_object_locations` | `get_object_locations(video_id, *, start_s=None, end_s=None, categories=None, main_only=False, with_face_points=False, max_frames=40 (≤ 200))` | Où sont les êtres vivants à chaque image clé (boîtes 0–1 et pixels). |
| `get_shots` | `get_shots(video_id, *, start_s=None, end_s=None, max_shots=60 (≤ 300))` | Plans, mouvement de caméra mesuré et ce qui s'y passe. |
| `get_synthesis` | `get_synthesis(video_id, *, min_usability=0, max_items=20)` | Titre, résumé, chapitres, moments forts (image et son), suggestions, utilisabilité des plans. |

## Chercher dans toute la bibliothèque

| Outil | Signature | Rôle |
|---|---|---|
| `search_memory` | `search_memory(query, *, kinds=None, video_id=None, timeline_id=None, date_from=None, date_to=None, place=None, weather=None, sun_phase=None, subjects=None, has_speech=None, limit=10 (≤ 50))` | Passages horodatés (vidéo, chapitre, plan, image, parole), par les mots et le sens. |
| `find_clips` | `find_clips(*, text=None, timeline_id=None, weather=None, sun_phase=None, place=None, date_from=None, date_to=None, subjects=None, shot_type=None, orientation=None, min_quality=None, has_speech=None, limit=20 (≤ 100))` | Des plans prêts pour Resolve : chemin, points de coupe, images, timecodes source. |
| `ask_library` | `ask_library(question, *, kinds=None, video_id=None, timeline_id=None, date_from=None, date_to=None, place=None, weather=None, sun_phase=None, subjects=None, shot_type=None, has_speech=None, visual_check=False)` | Réponse du modèle chargé, citée `[n]`, avec ses sources. |

## Monter avec DaVinci Resolve

| Outil | Signature | Rôle |
|---|---|---|
| `list_resolve_timelines` | `list_resolve_timelines()` | Le projet ouvert dans Resolve (lu par l'application, en lecture seule) et ses timelines : lesquelles sont dans la bibliothèque, lues quand, modifiées depuis (`changed_since_sync`), combien de vidéos analysées, introuvables ou hors bibliothèque. |
| `import_resolve_timeline` | `import_resolve_timeline(timeline_id=None, analyze=None, label=None)` | Ajoute la timeline (actuelle si `None`) à la bibliothèque, ou la met à jour : ses vidéos sont reliées, celles qui manquent ajoutées **seules** (jamais le reste de leur dossier) si la page Système autorise Claude à ajouter des dossiers (`mcp_add_folders`), sinon signalées « hors bibliothèque ». Demander à l'utilisateur d'abord. `analyze=None` garde le réglage de la timeline. Rend l'aperçu et le job (`get_job`). |
| `match_clips` | `match_clips(items)` — `items` : chemins, ou `{file_path, clip_uid?, source_start_s?, source_end_s?}` (ou `{source_start_frame?, source_end_frame?, fps?}`) (1 à 100) | Relie les clips d'une timeline aux vidéos analysées (chemin normalisé → identifiant du clip média d'une timeline ajoutée, même nom de fichier → nom et taille → empreinte du contenu → nom seul) ; avec une plage : plans, parole, sujet principal et ses boîtes, chapitres, moments forts, points de coupe sûrs. La plage se donne de préférence en **secondes du fichier** (`GetLeftOffset()` ÷ fps de la timeline, puis + `GetDuration()` ÷ fps de la timeline : les deux comptent en images de la timeline) : juste même quand la cadence du clip diffère de celle de la timeline ou qu'un fondu touche le clip (`GetSourceStartTime()`/`GetSourceEndTime()` comptent ses poignées). |
| `get_cut_points` | `get_cut_points(video_id, t_start, t_end)` | Entrée et sortie sûres : calées sur une coupe à moins de 0,5 s, jamais dans un mot ; J-cut/L-cut quand une phrase déborde ; en secondes, images et timecodes source. |
| `get_reframe` | `get_reframe(video_id, t_start, t_end, timeline_width, timeline_height, *, subject=None, headroom=None)` | Cadre fixe pour une autre forme de timeline : recadrage en pixels source et `ZoomX`, `ZoomY`, `Pan`, `Tilt` pour `SetProperties` (élément en `Scaling = Fit`) ; des segments si le sujet bouge trop. |
| `get_resolve_payload` | `get_resolve_payload(video_ids, include_shots=False, include_speech=False, include_metadata=True, language=None)` — `language` : `fr` ou `en` (défaut : langue de rédaction) | Le script Resolve figé v1 (ASCII) avec marqueurs et métadonnées comme seules données JSON, à passer tel quel à `run_script` du MCP de Resolve. Relancé, il remplace ses marqueurs sans toucher ceux de l'utilisateur. |
| `export_video` | `export_video(video_id, format, language=None)` — `srt`, `vtt`, `csv`, `chapters`, `edl`, `json`, `md`, `resolve` ; `language` : `fr` ou `en` | Écrit l'export dans `exports/<id vidéo>/` du dossier de données (jamais à côté de la vidéo) et rend son chemin ; le nom finit par sa langue (`<nom>_SHOTS_EN.csv`). |

Démarche type (voir l'invite `plan_edit`) :
1. `list_resolve_timelines` ; si la timeline n'est pas dans la bibliothèque (ou a changé), avec
   l'accord de l'utilisateur, `import_resolve_timeline` ; `list_watched(timeline_id=…)` ;
2. lire la timeline en direct avec le MCP de Resolve (identifiants, `File Path`, `FPS`,
   `GetLeftOffset()`, `GetDuration()`), puis `match_clips` pour savoir quelle analyse
   correspond à chaque clip et ce que contient sa plage ;
3. choisir les plans (`find_clips`, `get_synthesis`, `get_frames`) ;
4. `get_cut_points` pour chaque plan retenu ;
5. construire ou modifier une **copie** de la timeline avec le MCP de Resolve : coupes franches et
   fondus enchaînés seulement (`Cross Dissolve` pour l'image, `Cross Fade +3 dB` pour le son,
   `alignment: 'center'`), en images entières ;
6. `get_reframe` pour une autre forme (9:16…) puis `SetProperties` ;
7. `get_resolve_payload` puis `run_script` pour les marqueurs et métadonnées.

## Outils Resolve pour l'assistant

Actifs quand l'utilisateur coche « Outils Resolve pour l'assistant » dans la page Connexions
(préférence `mcp_resolve_tools`, désactivée par défaut) ; sinon chacun répond comment l'activer.
Ils passent par les scripts figés de l'application (processus enfant `fusionscript`, comme la
lecture des timelines) : l'assistant n'envoie que des données. Aucune timeline existante n'est
modifiée ; le projet n'est pas enregistré.

| Outil | Signature | Rôle |
|---|---|---|
| `read_timeline` | `read_timeline(timeline_id=None, include_audio=False)` | La timeline lue maintenant (celle ouverte par défaut), clip par clip : piste, place, plage en **secondes du fichier** (règle mesurée dans Resolve 21.1), identifiants du media pool et de la timeline, vidéo analysée (`video_id`, `status`). |
| `plan_reframe` | `plan_reframe(items, timeline_width, timeline_height, *, subject=None, heads=True, sheets="flagged", min_piece_s=2.5)` — `items` : `{video_id, in_s, out_s, anchor?, rotation?, subject?}` (1 à 200) | Cadres fixes par morceau, visant la **tête** du sujet (demandée au modèle de vision chargé pour les images clés où le sujet dépasse le cadre, gardée en cache), sans sauts (hystérésis, morceaux ≥ `min_piece_s`), notés image par image, avec drapeaux et **planches de contrôle** en images ; `edit_items` prêts pour `build_timeline`. `anchor` : `auto`, `center` (sujet vu d'en haut), `top`, `bottom` ; `rotation` : 0, 180, ±90 (rush filmé couché). |
| `build_timeline` | `build_timeline(name, items, timeline_width=None, timeline_height=None)` — `items` : `{media_pool_item_id? \| video_id? \| path?, in_s, out_s, video_only?, props?}` (1 à 500) | Une timeline **neuve** « name - vfe vN » : entrées et sorties converties en images à la cadence du clip lue dans Resolve, jamais au-delà de sa dernière image, lots de 50, fichiers absents importés dans le chutier « Video Frame Expedition », valeurs de Transform posées ; chaque durée et chaque valeur relues (`duration_ok`, `props_ok`). |
| `apply_markers` | `apply_markers(video_ids, include_shots=False, include_speech=False, include_metadata=True, language=None)` | Le script figé des marqueurs (celui de `get_resolve_payload`), exécuté par l'application. Les clips de timeline créés ensuite portent les marqueurs. |

Démarche avec ces outils : `read_timeline` → choisir les plans → `get_cut_points` →
`plan_reframe` (regarder les planches, régler `anchor`/`rotation`/`subject`, relancer) →
`build_timeline(name, edit_items)` → `apply_markers` avant la construction si les marqueurs
doivent être sur les clips de la nouvelle timeline. Rappeler à l'utilisateur d'enregistrer son
projet dans Resolve.

## Ressource

| URI | Contenu |
|---|---|
| `vfe://videos/{video_id}/manifest` | Le MANIFEST lisible (Markdown) d'une vidéo : fichier, contexte, résumé, chapitres, moments forts, plans ; dans la clôture non fiable. |

## Invites

| Invite | Arguments | Rôle |
|---|---|---|
| `plan_edit` | `goal`, `target_duration?`, `style?` | Préparer un montage dans DaVinci Resolve avec les deux serveurs MCP. |
| `review_rushes` | `folder?` | Passer en revue les rushs d'un dossier (ou de la bibliothèque) : plans à garder, moments forts, défauts. |
