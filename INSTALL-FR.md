# Guide d'installation — version française

Le reste du projet est en anglais (le rapport publié, le code, le README) parce
que ton audience l'est. Ce fichier-ci est pour toi : les étapes d'installation,
en français.

---

## Pourquoi GitHub

Un script, c'est une recette : il faut une machine allumée pour l'exécuter à 8 h
et à 22 h. GitHub prête des machines gratuitement et les réveille à l'heure dite.
Ton ordinateur peut rester éteint. En prime, tu obtiens une adresse web publique
fixe qui se met à jour toute seule — à mettre dans ta bio X.

## Les étapes

**1. Compte.** [github.com](https://github.com), crée un compte gratuit.

**2. Dépôt.** Bouton **+** en haut à droite → **New repository**. Nom :
`runners-tracker`. Coche **Public** (obligatoire pour la page gratuite). Ne coche
rien d'autre → **Create repository**.

**3. Fichiers.** Sur la page du dépôt vide, clique **uploading an existing file**.
Décompresse l'archive, glisse **tout son contenu** dans la zone. Attends la fin
du transfert → **Commit changes**.

> Le dossier `.github` est masqué par ton système. Sur Mac : `Cmd + Maj + .` dans
> le Finder. Sur Windows : onglet Affichage → cocher « Éléments masqués ».
> Sans ce dossier, rien ne se déclenchera jamais.

**4. Droits d'écriture.** Onglet **Settings** → **Actions** → **General** dans le
menu de gauche. Descends jusqu'à **Workflow permissions**, sélectionne
**Read and write permissions** → **Save**.

**5. Page publique.** Onglet **Settings** → **Pages**. Sous **Source**, choisis
**GitHub Actions**. Ton adresse : `https://TON-PSEUDO.github.io/runners-tracker/`.

**6. Premier lancement.** Onglet **Actions** → **Solana Runner Tracker** dans la
colonne de gauche → **Run workflow** → **Run workflow**. Deux à quatre minutes.
Point vert = c'est passé. Ouvre ton adresse.

Ensuite ça tourne tout seul à 8 h et 22 h.

---

## Tes 400 wallets

Prépare un fichier nommé exactement `kol_wallets.csv` :

```csv
address,label,tier,weight
7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU,Cented,S,3
9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM,Euris,A,2
```

Seule la colonne `address` est obligatoire. `tier` accepte S / A / B (poids 3 / 2
/ 1,5). Un wallet dont tu sais qu'il bouge réellement les prix mérite un poids
fort.

Dépose-le dans le dossier `config/` du dépôt : entre dans le dossier, puis
**Add file** → **Upload files**.

Quand tu auras vérifié que les résultats sont cohérents, ouvre
`config/settings.yaml`, clique sur le crayon, et passe `require_kol` à `true`.
À partir de là, aucun token n'entre dans les runners sans validation par tes
wallets. C'est le réglage qui transforme l'outil en filtre personnel.

---

## La clé Helius (49 $/mois, plus tard)

Sans elle, tout fonctionne sauf la confluence de wallets. Commence sans.

Quand tu la veux : [helius.dev](https://helius.dev) → crée un compte → copie la
clé API. Puis dans ton dépôt : **Settings** → **Secrets and variables** →
**Actions** → **New repository secret**. Nom : `HELIUS_API_KEY`. Valeur : ta clé.

Ne colle jamais une clé directement dans un fichier du dépôt : il est public.

---

## Passage à l'heure d'hiver

Les horaires sont en UTC. Fin octobre, ouvre `.github/workflows/tracker.yml`,
clique sur le crayon, remplace `0 6` par `0 7` et `0 20` par `0 21`.
C'est la seule maintenance de l'année.

---

## Régler les seuils

Tout est dans `config/settings.yaml`, avec un commentaire par ligne. Les deux
réglages qui changent vraiment le caractère du tracker :

- `min_liquidity_usd` — monte-le à 50000 pour ne garder que des tokens sur
  lesquels on peut réellement prendre une position
- `wash_reject` — descends-le à 40 pour être plus sévère sur le volume, monte-le
  à 70 pour laisser passer plus de candidats

---

## Ce que l'outil ne fait pas

- **Il ne prédit pas.** Il décrit ce qui s'est passé et ce qui se forme.
- **Il ne détecte pas tous les rugs.** Une équipe qui vend, un influenceur payé,
  un narratif qui meurt : les filtres écartent la fraude mécanique, pas les
  mauvaises intentions.
- **Un wash-trader compétent passe.** Les sept signaux attrapent les bots
  paresseux, qui sont la majorité. Quelqu'un qui varie ses montants et ses
  intervalles reste indétectable depuis des données publiques agrégées.
- **Ce n'est pas un conseil en investissement.** Ton audience prendra des
  décisions financières à partir de ce que tu publies. La section « Cut — and
  why » et la section performance sont là pour que tu restes honnête avec elle.
  C'est aussi, à long terme, ce qui construit une audience qui reste.
