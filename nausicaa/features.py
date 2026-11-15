"""Fonctionnalités facultatives de Nausicaa, toutes désactivables sans redémarrage.

Chaque fonctionnalité se consulte au moment de l'usage (enabled(id)), jamais à l'import :
Préférences → Fonctionnalités l'active ou la coupe tout de suite.
"""

from __future__ import annotations

from dataclasses import dataclass

from .i18n import tr
from .settings import settings


@dataclass(frozen=True)
class Feature:
    id: str
    title: str
    description: str
    icon: str
    default: bool = True


FEATURES = [
    Feature("click_pause", tr("Clic pour mettre en pause"),
            tr("Un clic gauche sur l'image met en pause ou relance, comme VLC ; un double-clic passe en "
               "plein écran sans mettre en pause"),
            "input-mouse-symbolic"),
    Feature("wheel_volume", tr("Volume à la molette"),
            tr("La molette de la souris sur l'image règle le volume, affiché par-dessus la vidéo"),
            "audio-volume-high-symbolic"),
    Feature("minimize_pause", tr("Pause quand la fenêtre est réduite"),
            tr("La lecture s'arrête quand la fenêtre est réduite et reprend quand elle revient"),
            "window-minimize-symbolic"),
    Feature("resume", tr("Reprendre où on s'était arrêté"),
            tr("Une vidéo rouverte repart de l'endroit où on l'avait quittée"),
            "document-open-recent-symbolic"),
    Feature("next_episodes", tr("Épisodes suivants"),
            tr("Ouvrir une seule vidéo ajoute à la file les autres vidéos de son dossier, dans l'ordre "
               "des noms (« Épisode 2 » avant « Épisode 10 »)"),
            "view-list-symbolic"),
    Feature("mpris", tr("Contrôle depuis le système"),
            tr("Panneau de GNOME, touches multimédia du clavier et boutons d'un casque Bluetooth (MPRIS)"),
            "input-keyboard-symbolic"),
    Feature("file_titles", tr("Titre enregistré dans le fichier"),
            tr("Afficher le titre que la vidéo porte en elle (« Série - S02E04 - Titre ») au lieu du nom du "
               "fichier, quand il en a un qui a du sens"),
            "text-x-generic-symbolic"),
    Feature("auto_fullscreen", tr("Plein écran à l'ouverture"),
            tr("Une vidéo ouverte (gestionnaire de fichiers, « Ouvrir », glisser) passe directement en plein "
               "écran, comme VLC ; pas un fichier audio, ni une vidéo ajoutée à la file"),
            "view-fullscreen-symbolic", default=False),
    Feature("hwdec", tr("Décodage matériel"),
            tr("La carte graphique décode la vidéo quand elle le peut : moins de travail pour le processeur"),
            "video-display-symbolic"),
]

BY_ID = {f.id: f for f in FEATURES}


def enabled(fid: str) -> bool:
    f = BY_ID.get(fid)
    if f is None:
        return False
    value = settings["features"].get(fid, f.default)
    return value if isinstance(value, bool) else f.default     # « "no" » écrit à la main ne vaut pas vrai


def set_enabled(fid: str, value: bool):
    feats = dict(settings["features"])
    feats[fid] = bool(value)
    settings["features"] = feats
