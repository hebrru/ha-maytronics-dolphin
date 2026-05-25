# Maytronics Dolphin BLE pour Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-03A9F4.svg)](https://www.home-assistant.io/)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-hebrru-FFDD00.svg?logo=buymeacoffee&logoColor=black)](https://www.buymeacoffee.com/hebrru)

Integration Home Assistant pour piloter en local les robots de piscine Maytronics Dolphin compatibles avec le protocole Bluetooth MyDolphin.

Le but est simple : garder le controle en local, sans cloud, avec des sessions BLE courtes pour eviter de bloquer le robot.

Depot HACS :

```text
https://github.com/hebrru/ha-maytronics-dolphin
```

## Ce que ca fait aujourd'hui

- Allumer et eteindre le robot depuis Home Assistant.
- Lire l'etat du robot : `off`, `on`, `hold`, `programming`, `self_test`.
- Lire le statut de travail : `at_work`, `finished`, `fault`, `unknown`.
- Lire le programme de nettoyage actif.
- Changer le mode de nettoyage : `Standard`, `Rapide`, `Fond seul`, `Ligne d'eau`, `Ultra`.
- Estimer la surface en cours : `floor`, `wall`, `waterline`, `unknown`.
- Lire et ecrire le planning hebdomadaire natif du robot.
- Regler les jours, heures et minutes du planning natif.
- Activer/desactiver la repetition hebdomadaire native.
- Synchroniser l'heure interne du robot.
- Regler la duree de cycle native.
- Regler le depart differe natif.
- Lire les fonctions declarees par le boitier : `filter_status`, `weekly_timer`, `delayed_start`, `speed`.
- Activer les fonctions du boitier si le firmware les accepte.
- Liberer la connexion Bluetooth quand le robot reste accroche.
- Utiliser un proxy Bluetooth Home Assistant / ESPHome pres de la piscine.
- Acceder a des boutons avances : ping, retour, reset defauts, reset filtre, joystick, test LED, test carte.

## Points importants

- Un seul client BLE a la fois : fermez MyDolphin pendant les tests Home Assistant.
- Le robot doit annoncer en Bluetooth avant qu'une commande fonctionne.
- Un proxy Bluetooth pres de la piscine change tout si le Raspberry est trop loin.
- L'integration connecte, envoie/lit, puis deconnecte. C'est volontaire.
- La mise a jour firmware OTA `fffb` n'est pas exposee.

## Installation HACS

1. Installez HACS si besoin.
2. Allez dans HACS -> Integrations -> menu `...` -> Custom repositories.
3. Ajoutez ce depot :

```text
https://github.com/hebrru/ha-maytronics-dolphin
```

4. Choisissez la categorie `Integration`.
5. Telechargez l'integration.
6. Redemarrez Home Assistant.
7. Allez dans Parametres -> Appareils et services -> Ajouter une integration.
8. Cherchez `Maytronics Dolphin (BLE)`.
9. Entrez l'adresse Bluetooth du robot, ou son nom BLE sur 12 caracteres si l'adresse est resolue par Home Assistant.

Exemples acceptes :

```text
AA:BB:CC:DD:EE:FF
A1B2C3D4E5F6
```

## Installation manuelle

Copiez le dossier :

```text
custom_components/maytronics_dolphin
```

dans :

```text
config/custom_components/maytronics_dolphin
```

Puis redemarrez Home Assistant.

## Entites principales

| Entite | Type | Role |
| --- | --- | --- |
| Alimentation | Switch | Demarrage / arret du robot |
| Mode de nettoyage | Select | Standard, Rapide, Fond seul, Ligne d'eau, Ultra |
| Etat du robot | Sensor | Etat PS_State du robot |
| Programme de nettoyage | Sensor | Programme BLE lu depuis le robot |
| Surface de nettoyage | Sensor | Detection best-effort sol / mur / ligne d'eau |
| Statut de travail | Sensor | Etat de travail pour dashboards et automations |
| Nettoyage actif | Binary sensor | Actif si le robot n'est pas completement eteint |
| PS state data OK | Binary sensor | Derniere lecture d'etat BLE reussie |
| Nettoyage auto | Switch | Commande autoclean experimentale |

## Planning natif

L'integration expose le programmateur interne du robot, pas seulement une automation Home Assistant.

Entites utiles :

| Entite | Type | Role |
| --- | --- | --- |
| Lire horaire natif | Button | Relit le planning stocke dans le robot |
| Envoyer horaire natif | Button | Ecrit les jours/heures/minutes dans le robot |
| Effacer horaire natif | Button | Vide le planning natif |
| Synchroniser heure robot | Button | Met l'horloge interne du robot a l'heure Home Assistant |
| Lundi...Dimanche horaire actif | Switch | Active le jour dans le planning natif |
| Lundi...Dimanche heure horaire | Number | Heure de depart pour ce jour |
| Lundi...Dimanche minute horaire | Number | Minute de depart pour ce jour |
| Repetition horaire native | Switch | Active la repetition hebdomadaire |
| Duree cycle native | Number | Duree de cycle ecrite dans le robot |
| Retard depart natif | Number | Depart differe natif |

Utilisation conseillee :

1. Appuyer sur `Lire horaire natif`.
2. Activer les jours voulus.
3. Regler heure et minute.
4. Activer `Repetition horaire native` si besoin.
5. Appuyer sur `Envoyer horaire natif`.

## Fonctions du boitier

Certains boitiers ont les icones imprimees mais les fonctions desactivees dans leur configuration interne.

L'integration peut lire :

- `filter_status`
- `weekly_timer`
- `delayed_start`
- `speed`

Le bouton `Activer fonctions boitier` envoie la commande BLE `Feature_Enable_OR_Disable`.

Sur un boitier compatible, cela peut activer :

- le programmateur hebdomadaire du boitier,
- le depart differe,
- le mode rapide,
- l'indication filtre.

Attention : cette commande ecrit dans la configuration du boitier d'alimentation. Elle est utile, mais elle doit etre utilisee consciemment.

## Boutons 1 / 2 / 3 du boitier

Sur les alimentations Maytronics compatibles, les boutons `1`, `2`, `3` correspondent au programme hebdomadaire du boitier :

- `1` : nettoyage tous les jours pendant une semaine.
- `2` : nettoyage un jour sur deux.
- `3` : nettoyage tous les trois jours.

Si les boutons ne reagissent pas, verifiez le capteur `Fonctions boitier`.

Si `weekly_timer` est a `false`, appuyez sur `Activer fonctions boitier`, puis relisez les fonctions. Un redemarrage du boitier peut etre necessaire selon le modele.

## Modes de nettoyage

| Option Home Assistant | Commande Dolphin |
| --- | --- |
| Standard | `regular` |
| Rapide | `fast_mode` |
| Fond seul | marqueur interne `climbing_wall_time = 234` |
| Ligne d'eau | `waterline` |
| Ultra | `ultraclean` |

`Ultra` est le mode intensif quand le robot le supporte. Selon le modele, il peut modifier le comportement de deplacement et d'aspiration pour un nettoyage plus pousse.

## Bluetooth et portee

Si les commandes sont lentes ou echouent :

- rapprochez le robot ou le proxy Bluetooth,
- fermez l'application MyDolphin,
- appuyez sur `Liberer Bluetooth`,
- attendez une annonce BLE du robot,
- verifiez Parametres -> Appareils et services -> Bluetooth.

Un proxy ESPHome peut etre ajoute avec :

```yaml
esp32_ble_tracker:
  scan_parameters:
    active: true

bluetooth_proxy:
  active: true
  connection_slots: 3
```

## Options de l'integration

| Option | Defaut | Role |
| --- | --- | --- |
| Intervalle de lecture de l'etat | 45 s | Frequence de lecture PS_State |
| Liberation Bluetooth periodique | 120 s | Deconnecte si une session reste ouverte |
| Session BLE persistante | Off | Experimental, peut bloquer certains robots |
| Bouton Liberer Bluetooth | On | Ajoute le bouton de liberation |
| Lectures diagnostic fffc/fffd | Off | A activer seulement pour debug |

## Debug

Ajoutez dans `configuration.yaml` :

```yaml
logger:
  default: info
  logs:
    custom_components.maytronics_dolphin: debug
```

## Compatibilite

Developpe a partir des trames de l'application Android MyDolphin 2.3.19 et teste sur un robot compatible BLE service `FFF0`.

Les modeles Dolphin ne reagissent pas tous exactement pareil. Si une commande ne fonctionne pas, ouvrez une issue avec :

- modele du robot,
- modele du boitier,
- etat des capteurs `Fonctions boitier`,
- logs debug Home Assistant,
- distance entre le robot et le proxy Bluetooth.

## Non expose volontairement

- Firmware / OTA `fffb`.
- Ecritures inconnues non validees.
- Commandes destructives non documentees.

## Soutenir le projet

Si cette integration vous aide, vous pouvez soutenir le travail ici :

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-hebrru-FFDD00.svg?logo=buymeacoffee&logoColor=black)](https://www.buymeacoffee.com/hebrru)

## Credits

Projet communautaire independant, sans affiliation officielle avec Maytronics.

Maytronics, Dolphin et MyDolphin sont des marques de leurs proprietaires respectifs.
