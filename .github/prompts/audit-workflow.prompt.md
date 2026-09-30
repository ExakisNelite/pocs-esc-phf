J'ai besoin de faire un audit de plusieurs GitHub Actions reusable workflows qui sont dans un repository sur une organisation d'entreprise.

J'ai besoin d'auditer pour chaque workflow a quoi ils servent, ce qu'ils font, leur paramètres d'entrée. 

On profitera de cet audit pour rédiger des pages markdown de documentation pour chaque workflow. Pour chaque élément, j'ai également besoin d'identifier s'il est utilisé/référencé par des workflows sur des repositories de l'organisation et par quels repository/workflow. Inclure tous les repositories de l'organisation : publics, privés, internes, archivés et forks. Détecter les références `uses: org/repo/.github/workflows/xxx.yml@<ref>` quelle que soit la ref (branche, tag ou SHA). Signaler explicitement les repositories inaccessibles et les limites de couverture, sans les considérer comme dépourvus de références.

Je ne veux pas que tu fasses cet audit ; je veux que tu me fournisses un plan d'implémentation complet pour que je le fasse moi-même. Les livrables attendus sont :
1. Les étapes du plan, avec des instructions claires pour chaque étape.
2. La collecte des informations nécessaires, avec des exemples précis d'appels API via `curl` en Bash. Sois exhaustif sur les étapes et fournis au moins un exemple de commande ou de script par étape de collecte, sans répéter les mêmes informations.
3. L'organisation des données collectées.
4. La vérification de l'exactitude des informations collectées.
5. Un modèle de documentation Markdown clair et structuré, avec des exemples concrets de fichiers Markdown à générer pour chaque workflow.
6. Des exemples de scripts ou commandes pour automatiser certaines parties de l'audit.
7. La gestion des erreurs et des cas particuliers.

Avant de détailler les commandes, fais préciser le contexte technique s'il n'est pas connu : GitHub Enterprise Cloud ou Server et URL de l'API, type d'authentification (PAT classique, token fine-grained ou GitHub App), outils disponibles parmi Bash, curl, jq et gh CLI. Précise les scopes ou permissions nécessaires selon le mode d'authentification retenu, sans demander de communiquer le token ou d'autres secrets.

Un premier plan d'implémentation a été rédigé ici [lien_vers_le_plan](../../scripts/audit-workflows/plan-implementation.md).

Lis d'abord le fichier `scripts/audit-workflows/plan-implementation.md`, puis repars de cette base en l'améliorant et en la détaillant pour couvrir tous les aspects mentionnés ci-dessus. Si tu ne peux pas y accéder, indique-le et propose un plan complet à partir de zéro, sans inventer le contenu du plan existant.
