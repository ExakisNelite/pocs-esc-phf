# Guide opérationnel : auditer les workflows avec l'outil Python

Ce document est la **procédure à suivre pour réaliser l'audit**. Il combine les opérations automatisées par l'outil et les vérifications humaines. Il ne présente pas des résultats d'audit.

| Document | Quand l'utiliser |
| --- | --- |
| Ce guide | Pour réaliser l'audit, dans l'ordre |
| [README.md](README.md) | Pour consulter les options, formats et limites techniques |
| [plan-implementation.md](plan-implementation.md) | Pour comprendre la conception et les extensions possibles ; pas comme procédure d'exécution |

**Il n'est pas nécessaire de créer les scripts Bash du plan.** Le moteur livré est [audit.py](audit.py). Python et `ruamel.yaml` suffisent ; Bash, curl, jq, yq et gh ne sont pas requis. Le wrapper [audit.sh](audit.sh) reste facultatif.

## 1. Comprendre le périmètre et le résultat attendu

L'audit porte sur les reusable workflows d'un repository bibliothèque et leurs consommateurs dans l'organisation.

| Automatisé par Python | À réaliser manuellement |
| --- | --- |
| Inventorier les repositories visibles et rapprocher un inventaire indépendant | Obtenir et faire valider cet inventaire par un propriétaire |
| Scanner les workflows des branches par défaut, archives et forks inclus | Valider le périmètre et traiter les accès manquants |
| Figer les commits, télécharger les YAML et vérifier leur identité de blob Git | Relire un échantillon de preuves aux mêmes commits |
| Extraire contrats, jobs, actions et appels de workflows | Décrire le fonctionnement, les effets et les prérequis |
| Résoudre branches, tags, SHA et dépendances imbriquées | Examiner les appels non résolus et les limites du graphe |
| Vérifier statiquement les inputs et mappings de secrets | Vérifier disponibilité des secrets, expressions et permissions effectives |
| Produire Markdown, CSV, JSON et observations heuristiques | Qualifier les risques, décider des actions et approuver les livrables |

Les branches **appelantes** autres que la branche par défaut et leur historique sont exclus. En revanche, une version **appelée** par tag, branche ou SHA est analysée à son commit résolu, même si elle n'est plus présente sur la branche par défaut de la bibliothèque.

Une référence statique ne prouve pas une exécution. L'outil ne lance aucun workflow collecté, ne modifie pas GitHub et ne collecte pas l'historique des runs. Il ne télécharge pas automatiquement les scripts locaux ni les implémentations des actions.

**Livrables attendus :** preuves privées, catalogue versionné des workflows, registre de couverture, consommateurs et anomalies, décisions humaines et synthèse approuvée.

## 2. Préparer le périmètre, les accès et la confidentialité

**Manuel, avant toute collecte réelle.**

1. Noter l'organisation cible, le repository bibliothèque, le responsable de l'audit et la date. Faire confirmer le périmètre « branches par défaut, tous les repositories appartenant à l'organisation ».
2. Demander à un propriétaire un inventaire indépendant, comprenant tous les repositories publics, privés, internes, archivés et forks. Il ne doit pas être limité par la visibilité de l'App d'audit. Faire confirmer sa date et sa source.
3. Faire confirmer que la GitHub App est installée sur **All repositories**, sans restriction du token à une sous-liste. Pour le socle, les permissions sont `Metadata: read` et `Contents: read` ; aucun droit d'écriture n'est nécessaire.
4. Préparer le mécanisme approuvé qui fournit le token d'installation. L'outil ne crée pas l'App, ne signe pas son JWT et ne gère pas sa clé privée.
5. Choisir un emplacement privé pour les résultats. Sur Windows, vérifier les ACL NTFS et les règles de synchronisation/sauvegarde. Ne pas supposer que `.gitignore` protège les fichiers contre les autres utilisateurs ou leur publication.

Ne jamais mettre de token dans la configuration, les commandes, les documents ou les décisions de revue. Ne pas activer une transcription du terminal pouvant exposer les secrets.

**Point de passage :** le périmètre et l'accès sont autorisés. Sans inventaire indépendant, l'audit peut avancer, mais son exhaustivité organisationnelle restera non vérifiée.

## 3. Préparer Python et vérifier l'outil hors ligne

Les commandes suivantes sont pour **PowerShell sous Windows**. Depuis la racine du repository, se placer dans le dossier de l'outil une seule fois :

```powershell
Set-Location scripts/audit-workflows
```

Toutes les commandes suivantes supposent ce dossier courant. Python 3.11+ est requis ; la version vérifiée dans ce workspace est Python 3.13.7.

```powershell
python --version
if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    python -m venv .venv
}
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
.\.venv\Scripts\python.exe audit.py --help
```

L'activation de l'environnement n'est pas nécessaire. L'installation nécessite l'accès à l'index de paquets approuvé ; les tests ne contactent pas GitHub. En cas d'échec, arrêter et corriger l'environnement avant de collecter.

**Facultatif :** découvrir les livrables avec une démonstration simulée :

```powershell
.\.venv\Scripts\python.exe audit.py demo --run-dir results/demo-guide-01
```

Ouvrir `results/demo-guide-01/docs/index.md`. Les erreurs et la couverture partielle sont intentionnelles dans cette démonstration. Si le dossier existe déjà, choisir un autre nom ou reprendre avec `resume` ; ne pas écraser ses résultats.

Pour Linux/WSL ou Git Bash, consulter les commandes d'environnement du [README.md](README.md). Ne pas réutiliser un environnement Windows dans WSL.

## 4. Préparer la configuration et l'inventaire

**Manuel.** Les noms et IDs ci-dessous sont fictifs et doivent être remplacés.

Créer une configuration locale à partir de [config.example.json](config.example.json), sans écraser une configuration existante :

```powershell
if (-not (Test-Path config.json)) {
    Copy-Item config.example.json config.json
}
New-Item -ItemType Directory -Force results/preparation | Out-Null
```

Placer l'export du propriétaire dans `results/preparation/organization-inventory.json`. Ce chemin reste sous `results/`, ignoré par Git. Le format attendu est un tableau JSON, avec au minimum un ID numérique unique et un nom complet par repository :

```json
[
  { "id": 123456, "full_name": "acme/ci-workflows" },
  { "id": 789012, "full_name": "acme/service-api" }
]
```

Dans `config.json`, renseigner la cible et le chemin de l'inventaire :

```json
{
  "org": "acme",
  "library": "acme/ci-workflows",
  "apiUrl": "https://api.github.com",
  "apiVersion": "2026-03-10",
  "expectedInventory": "results/preparation/organization-inventory.json",
  "maxDepth": 10,
  "maxEdges": 100000,
  "maxFileBytes": 2097152
}
```

`expectedInventory` est relatif au fichier de configuration. Vérifier manuellement que l'export est complet, lisible et concerne la même organisation. Si aucun inventaire n'est disponible, retirer ce champ et consigner la limitation ; ne pas fournir un inventaire réduit pour obtenir artificiellement une couverture complète.

Vérifier la configuration :

```powershell
.\.venv\Scripts\python.exe audit.py check --config config.json
```

**Attention :** `check` valide les options locales et charge le parseur. Il ne vérifie ni les droits GitHub, ni la validité du token, ni l'exhaustivité ou le contenu de l'inventaire indépendant.

## 5. Fournir l'authentification

**Manuel, via le mécanisme approuvé.** Choisir une des deux possibilités :

| Possibilité | Action |
| --- | --- |
| Token dans l'environnement | Injecter `GITHUB_TOKEN` ou `GH_TOKEN` dans le processus qui lance Python, sans afficher sa valeur |
| Fournisseur de tokens | Ajouter un `tokenCommand` approuvé et effectivement installé, capable de retourner un token ou un JSON avec `token` et éventuellement `expires_at` |

`tokenCommand` est un tableau d'arguments, pas une commande shell. Il n'existe pas de fournisseur intégré. Le format et le renouvellement sont décrits dans le [README.md](README.md). Ne pas y mettre de secret en argument.

Un token expiré doit être renouvelé. Pour utiliser un fournisseur lors d'une reprise, repasser `--config config.json` ; son programme n'est pas conservé dans le manifeste des résultats.

**Point de passage :** les credentials sont disponibles dans le bon terminal, mais ne sont présents dans aucun fichier de configuration ou livrable.

## 6. Lancer la collecte automatisée

Choisir un **nouveau dossier vide** pour chaque nouvel audit. Le nom suivant est un exemple daté, à adapter :

```powershell
.\.venv\Scripts\python.exe audit.py run --config config.json --run-dir results/audit-2026-09-30-01
$LASTEXITCODE
```

L'outil inventorie les repositories, fige les commits, extrait les workflows, résout les appels et leurs contrats, puis génère les rapports. Il conserve également les erreurs et les références des fichiers lisibles lorsqu'une collecte est partielle.

| Code de sortie | Décision |
| --- | --- |
| `0` | Les contrôles automatisés de collecte sont passés ; poursuivre la revue humaine |
| `1` | Échec bloquant ; lire l'erreur, corriger sa cause et reprendre si un manifeste existe |
| `2` | Les rapports sont produits, mais la couverture est incomplète ou des contrats sont invalides ; examiner les rapports avant de conclure |

Un `0` n'est pas une approbation de sécurité. Les commandes `check`, `demo` et `report` peuvent retourner `0` malgré des anomalies dans les données.

Ne pas lancer deux collecteurs dans le même dossier. Ne pas corriger les workflows source pendant cette collecte en espérant que les snapshots enregistrés changent.

## 7. Vérifier et traiter la couverture en premier

**Manuel, à partir des rapports automatisés.** Ouvrir d'abord `reports/coverage.md` dans le dossier du run, puis `reports/coverage.csv` et `reports/inaccessible-repositories.csv`.

| État par repository | Interprétation et action |
| --- | --- |
| `complete` | Tous les fichiers découverts ont été analysés au commit figé ; passer à la revue |
| `no_workflows` | Le parcours complet n'a trouvé aucun fichier YAML de workflow dans ce snapshot |
| `empty` | GitHub a explicitement confirmé un repository Git vide |
| `partial` | Lire les erreurs ; résoudre les problèmes de fichier, arbre, parsing ou limite |
| `inaccessible` | Faire vérifier les accès et politiques ; un `404` ne prouve pas une absence |
| `not_visible_to_token` | Repository attendu mais absent de l'inventaire visible ; faire corriger l'accès et effectuer une nouvelle découverte |

Pour les états incomplets, `referenceCount: null` signifie **inconnu**, pas zéro. `referencesFound` conserve uniquement les appels trouvés dans les fichiers lisibles.

Vérifier séparément dans `data/audit.json` :

| Indicateur | Condition pour considérer cette partie complète |
| --- | --- |
| `organizationCoverage` | `complete_against_supplied_inventory` ; le propriétaire doit aussi confirmer la qualité de l'inventaire fourni |
| `dependencyCoverage` | `complete` ; aucun appel non résolu, cycle ou limite de graphe |

Une organisation complètement scannée peut avoir un graphe incomplet, et inversement. Vérifier les repositories supplémentaires visibles mais absents de l'export : création, transfert ou inventaire périmé à faire expliquer par le propriétaire.

**Point de passage :** chaque trou de couverture est corrigé ou explicitement documenté. Si la couverture reste incomplète, ne jamais conclure globalement « ce workflow n'est utilisé nulle part ».

## 8. Reprendre ou démarrer un nouveau run

| Situation | Action |
| --- | --- |
| Interruption, token expiré, quota, échec temporaire ou fichier non téléchargé | Corriger la cause, renouveler l'authentification si nécessaire, puis `resume` |
| Repository absent de l'inventaire visible, inventaire indépendant corrigé ou nouveaux repositories | Nouveau `run` : l'inventaire existant est figé |
| Workflow corrigé sur GitHub, branche/tag déplacé, nouvelle bibliothèque ou nouvelles limites | Nouveau `run` pour analyser le nouvel état |
| Besoin de régénérer uniquement les documents à partir des données déjà collectées | `report`, sans token ni accès GitHub |

```powershell
.\.venv\Scripts\python.exe audit.py resume --run-dir results/audit-2026-09-30-01 --config config.json
.\.venv\Scripts\python.exe audit.py report --run-dir results/audit-2026-09-30-01
```

`resume` conserve l'inventaire, les snapshots déjà enregistrés et les résolutions réussies. Un objet jamais résolu ou un repository sans snapshot peut être résolu pour la première fois lors de la reprise. Il ne s'agit pas d'un snapshot atomique de l'organisation.

Si `data/audit.json` n'a pas encore été produit, reprendre la collecte avant de demander les rapports. Si `.audit.lock` bloque la reprise, vérifier réellement qu'aucun collecteur ne tourne avant de supprimer ce verrou. Ne pas supprimer les caches ou checkpoints pour forcer un rafraîchissement.

## 9. Examiner les consommateurs et les contrats

**Automatisé :** extraction des appels, résolution des versions et premiers contrôles. **Manuel :** vérification et qualification des résultats.

Lire les fichiers dans cet ordre :

| Fichier dans le run | Travail à effectuer |
| --- | --- |
| `reports/unresolved-references.csv` | Examiner toutes les références non résolues, y compris celles vers des wrappers externes à la bibliothèque |
| `reports/validation-findings.csv` | Relire les contrats invalides et les valeurs nécessitant une revue au SHA effectivement appelé |
| `reports/consumer-references.csv` | Identifier appelants, jobs, refs, commits et routes directes/transitives vers la bibliothèque |
| `docs/index.md`, puis `docs/workflows/` | Examiner chaque version documentée de la bibliothèque, y compris les versions historiques |

Pour chaque appel problématique, retrouver le workflow appelant et le workflow appelé dans `data/audit.json`, puis leurs fichiers privés indiqués par `evidence`. Vérifier le job, les clés `with`, les mappings de secrets et le contrat **au commit appelé**, pas au `main` actuel.

| Résultat de validation | Interprétation |
| --- | --- |
| `invalid` | Anomalie statique à vérifier et attribuer : input absent/inconnu, mauvais type, secret mal mappé ou cible non réutilisable |
| `needs_review` | Expressions ou disponibilité des secrets inconnues ; une vérification humaine reste nécessaire |
| `static_checks_passed` | Les seuls contrôles statiques implémentés sont passés ; pas une garantie d'exécution |

Les valeurs littérales `false`, `0` et `""` sont conservées. Une expression ne prouve ni une valeur ni un type à l'exécution. Un mapping de secret ou `inherit` ne prouve pas sa disponibilité.

Une route de profondeur `1` correspond à un appel depuis un workflow racine ; une profondeur supérieure montre un chemin transitif. Examiner toute la route. Ne pas confondre nombre de routes, sites d'appel, fichiers appelants et repositories consommateurs uniques. Plusieurs versions d'un workflow peuvent produire plusieurs pages.

Pour une absence de consommateurs, utiliser une formulation limitée au périmètre et à la couverture vérifiés, par exemple : « Aucun appel détecté dans les snapshots complètement scannés ; les accès et dépendances non résolus restent à examiner. »

## 10. Réaliser la revue fonctionnelle et de sécurité

**Manuel, pour chaque workflow/version significatif.** L'objectif et le fonctionnement ne sont pas inférés automatiquement à partir du nom du workflow.

1. **Fonction et effets :** lire les commandes `run`, l'ordre `needs`, les conditions, matrices et stratégies. Décrire ce qui compile, teste, publie, déploie ou modifie un système externe ; distinguer constat et hypothèse.
2. **Prérequis :** identifier runners, outils, réseau, registres, variables, environnements, accès externes, timeouts et comportement en cas d'échec.
3. **Dépendances :** examiner les actions et scripts locaux/composites au commit approprié, ainsi que les containers si pertinents. L'outil ne les télécharge pas. Un `checkout` peut viser le repository consommateur : vérifier `repository`, `ref`, `path` et le répertoire de travail avant d'attribuer un script à la bibliothèque.
4. **Contrat :** relire inputs, valeurs par défaut, secrets et chaîne des outputs workflow → job → step. Vérifier aussi les conditions qui peuvent empêcher la production d'un output.
5. **Sécurité :** examiner injection de données dans le shell, références mutables, actions non épinglées, runners self-hosted et justification des permissions d'écriture ou d'OIDC.
6. **Configuration GitHub :** faire confirmer par les responsables les permissions effectives, politiques de partage, restrictions organisation/entreprise, protections d'environnements et disponibilité des secrets, sans collecter leurs valeurs. Les droits de l'App d'audit sont distincts de ceux des workflows audités.

Utiliser `reports/security-observations.csv` comme **liste de pistes**, pas comme verdict. Un input apparemment inutilisé peut être accédé indirectement ; des permissions d'écriture peuvent être nécessaires. L'absence d'observation n'est pas une approbation.

Attribuer une sévérité selon le contexte réel et documenter une action, un responsable et une échéance pour chaque anomalie confirmée. Toute modification des workflows ou des politiques fait l'objet d'une action séparée, autorisée ; elle n'est pas réalisée par l'outil d'audit.

**Facultatif, après accord :** compléter par actionlint, zizmor, ShellCheck ou un scanner de secrets approuvé. Ils ne sont ni installés ni lancés automatiquement. L'étude des exécutions réelles est un autre volet, à définir avec une période et des permissions supplémentaires ; ne pas déduire l'activité réelle des seules références statiques.

## 11. Enregistrer les décisions sans perdre la revue

Utiliser `review-notes.csv`, à la racine du run, pour la revue humaine. Ses colonnes sont :

| Colonne | Contenu à renseigner |
| --- | --- |
| `workflow` | Conserver l'identité exacte repository/chemin@SHA |
| `purpose` | Fonction validée, sans extrapolation depuis le nom |
| `effects` | Effets externes, publications, déploiements ou modifications |
| `prerequisites` | Runners, configuration et accès nécessaires, sans valeurs secrètes |
| `reviewer` | Identité du relecteur responsable |
| `decision` | Décision humaine explicite, par exemple validé, actions requises ou non vérifiable |
| `notes` | Preuves, date de revue, réserves, liens vers tickets, responsables et échéances |

L'outil ne remplace pas ce fichier et **n'intègre pas automatiquement ses décisions aux pages générées**. Ajouter manuellement une ligne si une reprise découvre une nouvelle version ; ne pas supprimer le fichier pour obtenir de nouvelles lignes au prix des décisions existantes.

Ne pas éditer les pages auto-générées pour y conserver la revue : `resume` et `report` les réécrivent. Préparer séparément une synthèse humaine, par exemple `synthese-audit.md` dans le dossier du run. Conserver une sauvegarde contrôlée des décisions.

## 12. Contrôler, conclure et partager

**Manuel, avant remise des livrables.**

- [ ] Chaque repository attendu ou découvert possède un état de couverture ; chaque trou a une explication.
- [ ] Les chiffres distinguent repositories, workflows, versions, sites d'appel et routes, sans additionner archives/forks/visibilités comme des catégories disjointes.
- [ ] Un échantillon représentatif des YAML et des contrats a été relu indépendamment au même SHA, comprenant les types de refs et de repositories présents dans l'audit.
- [ ] Les contrats invalides et toutes les références non résolues ont une décision ou une réserve explicite.
- [ ] Le fonctionnement, les effets, les prérequis et les risques sont relus avec les mainteneurs.
- [ ] Les décisions sont associées aux bonnes versions, avec relecteur, date et actions suivies.
- [ ] La synthèse ne présente ni une référence comme preuve d'exécution, ni un inconnu comme zéro, ni des heuristiques comme findings confirmés.
- [ ] Les données à partager ont été revues pour confidentialité et approuvées par le responsable.

La synthèse humaine doit contenir :

| Section | Contenu |
| --- | --- |
| Périmètre et méthode | Organisation, bibliothèque, branches appelantes, date, configuration et emplacement des preuves |
| Couverture | Inventaire de référence, états organisation/graphe, repositories et dépendances non vérifiés |
| Catalogue | Workflows/version, fonction, principaux consommateurs et responsabilités |
| Constats | Anomalies confirmées, risques contextualisés et preuves au SHA |
| Plan d'action | Priorité, responsable, échéance et critère de validation |
| Réserves et approbation | Limites non levées, décisions des mainteneurs et statut de clôture |

Un audit peut être remis avec des réserves, mais elles doivent être acceptées explicitement. « Collecte complète », « revue humaine terminée » et « corrections réalisées » sont trois états différents.

Les preuves brutes, `data/audit.json` et les CSV peuvent contenir des données sensibles, y compris des secrets codés en dur dans les sources. Les rapports omettent certaines valeurs mais ne sont pas un nettoyage complet des secrets. Ne partager que la sélection approuvée ; conserver l'ensemble privé selon la politique de rétention interne. Aucun envoi automatique n'est effectué.

Pour vérifier des corrections ultérieures, lancer un **nouveau run**, comparer les résultats et faire valider la clôture des actions. Ne pas utiliser `resume` comme rafraîchissement des workflows corrigés.

## Repères rapides

| Besoin | Commande ou fichier |
| --- | --- |
| Vérifier les options sans GitHub | `audit.py check --config config.json` |
| Nouveau snapshot | `audit.py run --config config.json --run-dir results/<nouveau-run>` |
| Compléter un run existant | `audit.py resume --run-dir results/<run> --config config.json` |
| Régénérer sans réseau | `audit.py report --run-dir results/<run>` |
| Premier rapport à lire | `reports/coverage.md` |
| Décisions humaines | `review-notes.csv` et synthèse séparée |

Préfixer les commandes par `.\.venv\Scripts\python.exe` depuis le dossier de l'outil. Pour les détails d'erreurs HTTP, options et formats, revenir au [README.md](README.md), pas aux scripts conceptuels du plan.
