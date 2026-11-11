# 0001. Publier MIDI vers MP3 sur GitHub avec un exécutable Windows léger et une interface affinée

**Date**: 2026-11-11
**Status**: Proposed

## Summary

Vous publiez le projet dans un dépôt GitHub public nommé `midi-to-mp3`, sous licence MIT, avec un mode d'emploi complet. Vous fournissez aussi un seul fichier `MIDI-vers-MP3.exe` (environ 15 Mo) qui lance le serveur et ouvre le navigateur. Cet exécutable léger n'embarque ni FluidSynth ni ffmpeg : un script `installer-dependances.ps1` les installe en un clic. En parallèle, l'interface est affinée à partir d'une critique de design : même identité visuelle, mais une liste de fichiers mieux placée, de vraies balises de progression et des réglages regroupés.

## Context

> ⚠️ Premise note: ce sujet mélange trois chantiers (distribution Windows, publication GitHub, refonte de l'interface). Ce spec les garde ensemble parce que la direction du design est déjà tranchée (faire évoluer l'existant) et qu'aucun des trois ne change l'architecture du serveur. Le risque à surveiller est ailleurs : un exécutable qui dépend de deux outils installés à part déçoit vite un utilisateur non technique. Le script d'installation et des messages d'erreur clairs sont donc des exigences, pas des bonus.

L'application tourne aujourd'hui avec `python app.py` et demande Python, Flask, FluidSynth et ffmpeg dans le PATH (la liste des dossiers où Windows cherche les programmes). Elle n'est pas sous Git, n'a pas de licence et son mode d'emploi suppose un utilisateur à l'aise avec le terminal.

Un exécutable créé avec PyInstaller (un outil qui emballe Python et le code dans un `.exe`) fige les chemins : le dossier du script n'existe plus, les fichiers sont décompressés dans un dossier temporaire à chaque lancement. Le code actuel calcule plusieurs chemins à partir de `__file__` (l'emplacement du fichier Python), ce qui casse dans ce mode.

Un dépôt public implique des règles : pas de binaire lourd dans l'historique Git, pas de soundfont de 31 Mo, pas de donnée personnelle dans les commits, et des licences tierces à respecter.

Si rien n'est fait, le projet reste un dossier local que personne d'autre ne peut installer sans aide.

## Requirements

**User stories**:
- En tant qu'utilisateur Windows sans Python, je veux double cliquer sur un `.exe` pour ouvrir l'interface, afin de convertir mes MIDI sans rien compiler.
- En tant qu'utilisateur, je veux un script qui installe FluidSynth, ffmpeg et une soundfont, afin de ne pas chercher chaque outil moi même.
- En tant que visiteur de GitHub, je veux un mode d'emploi, une licence et des tests, afin de décider si je peux utiliser et modifier le projet.
- En tant qu'utilisateur, je veux voir mes fichiers là où je les dépose et suivre la progression clairement, afin de ne pas me perdre dans la page.

**Acceptance criteria** (le contrat, chaque critère est vérifiable seul):
- **AC-1**: Sur une machine où FluidSynth, ffmpeg et une soundfont sont installés, double cliquer `MIDI-vers-MP3.exe` ouvre une fenêtre console qui affiche l'adresse locale, ouvre le navigateur par défaut sur l'application, et une conversion MIDI vers MP3 réussit de bout en bout. Aucune installation de Python n'est nécessaire.
- **AC-2**: Si FluidSynth ou ffmpeg manque, la console nomme l'outil absent, indique de lancer `installer-dependances.ps1`, et reste ouverte jusqu'à une touche pressée (elle ne se ferme pas aussitôt).
- **AC-3**: Si le port 5000 est occupé, l'exécutable prend le port libre suivant et affiche et ouvre la bonne adresse.
- **AC-4**: `installer-dependances.ps1` (lancé d'un double clic par `installer-dependances.bat`) installe ffmpeg et FluidSynth s'ils manquent, les ajoute au PATH utilisateur, télécharge une soundfont dans `%USERPROFILE%\soundfonts`, ne demande pas les droits administrateur, et peut être relancé sans rien casser (il saute ce qui est déjà en place).
- **AC-5**: Une seule commande (`build_windows.ps1`) produit `MIDI-vers-MP3-windows.zip` contenant l'exécutable, le script d'installation (`.ps1` et `.bat`), un `LISEZMOI.txt`, la licence et les mentions des composants tiers. Le `.exe` fait moins de 30 Mo.
- **AC-6**: Le dépôt public `midi-to-mp3` existe, sous licence MIT, avec un README (français, plus une courte section anglaise) qui couvre l'installation, l'utilisation, la construction de l'exécutable et les limites. Il ne contient ni binaire, ni soundfont, ni secret, ni donnée personnelle. Depuis un clone neuf, `pip install -r requirements.txt` puis `python -m unittest` passent.
- **AC-7**: Une Release GitHub `v1.0.0` joint le zip Windows, ses notes de version et son empreinte SHA256.
- **AC-8**: Dans l'interface, la liste des fichiers s'affiche juste sous la zone de dépôt (dans l'étape 1). L'étape de conversion montre un bouton qui indique le nombre de fichiers et la progression globale.
- **AC-9**: Les barres de progression sont des balises `<progress>` natives, libellées. La fin d'un traitement est annoncée dans une zone `aria-live="polite"`. Avec `prefers-reduced-motion`, l'animation indéterminée est ralentie, jamais supprimée.
- **AC-10**: Les réglages sont regroupés en « Son » et « Export ». Le dossier de sortie et le sous dossier tiennent sur une ligne compacte. La fenêtre de choix de dossier suit les règles du `<dialog>` natif : nom accessible, `autofocus`, fermeture par `Esc` et clic extérieur, aucun `role="dialog"` ni `aria-modal` ajouté.
- **AC-11**: Dans les thèmes clair et sombre, le texte atteint un contraste de 4,5 pour 1 et les composants d'interface 3 pour 1. Les boutons font au moins 40 px de haut.
- **AC-12**: Rien de l'existant ne régresse : les 19 tests actuels passent, et le dépôt de plusieurs fichiers, le choix de dossier et la progression réelle fonctionnent toujours.

## Options considered

### Option 1: Dossier portable en zip, outils embarqués

PyInstaller emballe Python, Flask, FluidSynth, ffmpeg et la soundfont. L'utilisateur dézippe et double clique.

**Pros**:
- Rien à installer, meilleure expérience.

**Cons**:
- 150 à 250 Mo à publier à chaque version.
- Redistribuer ffmpeg et FluidSynth impose de joindre leurs licences et d'indiquer où trouver le code source.

### Option 2: Un seul `.exe` léger, outils installés à part (choisie)

Le `.exe` contient seulement Python, Flask et le code (environ 15 Mo). FluidSynth, ffmpeg et la soundfont s'installent avec un script fourni.

**Pros**:
- Petit, rapide à publier, aucune redistribution de binaires tiers.
- Les outils se mettent à jour séparément.

**Cons**:
- Une étape d'installation de plus, donc un risque d'abandon pour un utilisateur non technique.
- Plus de cas de panne : outil absent, PATH non rechargé.

### Option 3: Installateur `Setup.exe` (Inno Setup)

Comme l'option 1, dans un installateur avec raccourci et désinstallation.

**Pros**:
- Aspect le plus professionnel.

**Cons**:
- Une brique de plus à construire et à tester, pour un projet qui démarre.

## Decision

**Chosen option**: Option 2: Un seul `.exe` léger, outils installés à part.

Vous livrez `MIDI-vers-MP3.exe` (PyInstaller, mode un fichier, fenêtre console) dans un zip avec `installer-dependances.ps1`, et vous publiez le tout dans un dépôt public MIT, avec une Release v1.0.0.

**Implementation skills**: `modern-web-guidance` (guides `dialog`, `progress`, `switch`, `brand-consistent-forms`) · `design-critique` (critique de l'interface actuelle).

## Rationale

Vous avez choisi la légèreté plutôt que le confort. Cela a un avantage concret ici : la licence. Ne pas embarquer ffmpeg (souvent compilé en GPL) ni FluidSynth (LGPL) évite d'avoir à joindre leur code source et leurs avis dans chaque version. Le zip ne contient que votre code, Flask (BSD) et le chargeur de PyInstaller, dont la licence autorise la distribution du programme produit.

Le coût, c'est l'installation des deux outils. Pour le réduire, le spec demande trois choses : un script idempotent qui fait tout sans droits administrateur (AC-4), des messages d'erreur qui disent quoi faire (AC-2), et une soundfont posée dans `%USERPROFILE%\soundfonts`, un dossier que le moteur scanne déjà. Si les retours montrent que l'installation décourage, passer à l'option 1 ne change pas l'architecture : on ajoute les binaires au zip.

Mode console plutôt que fenêtre native ou icône : c'est le plus fiable. Fermer la fenêtre arrête le serveur, sans processus caché. Le port libre automatique évite l'erreur la plus fréquente d'un serveur local.

## Feature design

**Data model sketch**: aucune base de données. Les tâches de conversion restent en mémoire (6 heures), comme aujourd'hui. Rien à migrer.

**State transitions**: inchangées (`queued`, `synth`, `encode`, `done`, `error`).

**API surface**: inchangée, avec un seul champ ajouté sans risque.

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| /api/config | GET | aucun | output_root, max_upload_mb, can_browse, version | aucune (local) | aucune |

Les autres routes (`/api/soundfonts`, `/api/jobs`, `/api/jobs/<id>`, `/api/jobs/<id>/audio`, `/api/browse`, `/api/mkdir`) ne changent pas.

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| Exécutable au lancement | Chemin de `index.html` | Dossier d'extraction de PyInstaller (`sys._MEIPASS`) quand l'appli est figée, dossier du script sinon |
| Exécutable au lancement | Dossier des soundfonts | `%USERPROFILE%\soundfonts` (déjà scanné), puis un dossier `soundfonts` à côté de l'exe |
| Exécutable au lancement | Dossier de sortie par défaut | `--output-dir` si donné, sinon `Musique\MIDI vers MP3` de l'utilisateur quand figé, `./output` en développement |
| Exécutable au lancement | Port | 5000, sinon premier port libre trouvé par un essai de liaison |
| Interface | Numéro de version affiché | Constante `__version__` dans `app.py`, exposée par `/api/config` |
| Script d'installation | Dernière version de FluidSynth | API publique des Releases du dépôt FluidSynth, archive `win10-x64` |
| Script d'installation | Soundfont | Fichier `GeneralUser-GS.sf2` du dépôt public GeneralUser GS |

**Key invariants**:
- Le zip et le dépôt ne contiennent jamais FluidSynth, ffmpeg ni de soundfont.
- Le sélecteur de dossier reste actif seulement pour un serveur écoutant en local (règle existante, inchangée).
- `installer-dependances.ps1` ne modifie que le PATH utilisateur et `%USERPROFILE%`, jamais le PATH machine.

**Security model**: serveur local uniquement par défaut, contrôles d'origine et d'hôte déjà en place. L'exécutable n'est pas signé : Windows SmartScreen affichera un avertissement au premier lancement, à documenter dans le README. Aucune donnée personnelle ni secret dans le dépôt ni dans les commits (adresse de contact GitHub de type noreply).

**Configuration required**:
- `--output-dir`: dossier racine des MP3 (existant).
- `--no-browser`: ne pas ouvrir le navigateur au lancement (nouveau, utile en développement).

**Critical test scenarios**:
- Happy path: lancer l'exe construit, convertir un MIDI, retrouver le MP3 dans le dossier choisi, verifies **AC-1**, **AC-12**
- Failure case: lancer l'exe avec `PATH` privé de ffmpeg, la console nomme ffmpeg et attend une touche, verifies **AC-2**
- Failure case: occuper le port 5000 puis lancer, l'adresse affichée utilise un autre port et répond, verifies **AC-3**
- Idempotence: lancer deux fois `installer-dependances.ps1`, la seconde fois ne réinstalle rien, verifies **AC-4**
- Fresh clone: cloner le dépôt public dans un dossier vide, installer les dépendances, lancer les tests, verifies **AC-6**
- UI: contrôle visuel clair et sombre, 390 px et 1000 px de large, navigation au clavier de la fenêtre de dossier, verifies **AC-8** à **AC-11**

## Build plan

Approche par tranches de bout en bout (aucune approche enregistrée dans le projet, hypothèse notée : tracer bullet, on pose d'abord un fil mince qui traverse tout, puis on l'épaissit).

1. Rendre `app.py` et `midi2mp3.py` conscients du mode figé : chemins de ressources, dossier de sortie par défaut, port libre, ouverture du navigateur, option `--no-browser`, pause en cas d'erreur de démarrage, `version` dans `/api/config`, avec tests, satisfies **AC-1**, **AC-2**, **AC-3**, **AC-12**
2. Écrire `build_windows.ps1` (PyInstaller, un fichier, console) et construire un premier exe, puis le lancer et convertir un MIDI avec, satisfies **AC-1**, **AC-5**
3. Écrire `installer-dependances.ps1` et le tester ici, y compris une seconde exécution, satisfies **AC-4**
4. Appliquer les améliorations d'interface issues de la critique et des guides web (liste sous la zone de dépôt, `<progress>`, regroupement des réglages, ligne de destination compacte, `<dialog>` conforme, interrupteurs `switch`, contrastes et tailles), puis vérifier les deux thèmes et deux largeurs, satisfies **AC-8**, **AC-9**, **AC-10**, **AC-11**, **AC-12**
5. Préparer le dépôt : `LICENSE` MIT, `.gitignore`, `requirements-dev.txt`, `THIRD_PARTY_NOTICES.md`, README complet (français, section anglaise courte), `LISEZMOI.txt` du zip, satisfies **AC-6**
6. Reconstruire l'exe depuis le code final, assembler le zip, calculer le SHA256, satisfies **AC-5**, **AC-7**
7. Installer GitHub CLI, authentifier le compte (étape qui demande une action de votre part), créer le dépôt public `midi-to-mp3`, pousser, puis créer la Release v1.0.0 avec le zip, satisfies **AC-6**, **AC-7**

## Consequences

**Positive**:
- N'importe qui peut essayer l'application sans Python.
- Aucun binaire tiers à redistribuer, donc des obligations de licence légères.
- Le dépôt reste petit et se clone vite.

**Negative / tradeoffs**:
- L'installation des outils est une étape manuelle de plus, et le PATH n'est pris en compte qu'après réouverture du terminal ou relance de l'exe.
- L'exe n'est pas signé : SmartScreen et certains antivirus peuvent l'avertir ou le signaler à tort (fréquent avec PyInstaller en mode un fichier).
- En mode un fichier, chaque lancement décompresse l'exe dans un dossier temporaire, ce qui ajoute quelques secondes.
- Python 3.15 est très récent : PyInstaller peut ne pas le supporter encore, il faudra alors construire avec une version de Python plus ancienne.

**Neutral**:
- Le projet passe sous Git pour la première fois, avec une histoire propre à partir de la v1.0.0.
- Les specs (`docs/specs/`) sont publiées avec le code.

## Follow-up

- [ ] Tests automatiques et construction du zip par GitHub Actions : non choisis pour l'instant, à reconsidérer quand les mises à jour deviendront fréquentes.
- [ ] Signature de code du `.exe` pour supprimer l'avertissement SmartScreen (coût et démarches à évaluer).
- [ ] Mesurer si l'étape d'installation décourage les utilisateurs, et passer à l'option 1 (outils embarqués) si oui.
- [ ] Envisager un bouton « Afficher dans l'explorateur » pour ouvrir le dossier du MP3 produit (non demandé, idée issue de la critique de design).
- [ ] Ajouter un fichier `AGENTS.md` décrivant la pile du projet (aucun n'existe).
