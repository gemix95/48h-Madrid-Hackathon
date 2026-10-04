"""Easter eggs: one line of Madrid lore in the first message of a new dealer conversation, each line once.

Saturday's feed shows what the dealers answer to (other teams' words are hidden, the dealers' replies are not):
  - Abuela: the swap chant «sile, nole, repe, me falta», the chotis danced on one tile and her saint's day gave
    t08 and t02 the "Castizo" badge; «cocido con sus tres vuelcos» made her give t10 and t08 a duplicate card;
  - El Chato: «Plaza Mayor, con caña» / a vermut gave t10 and t08 a pack;
  - Pícaros: naming Lazarillo, Rinconete or «el timo de la estampita» gave six teams "Trickster tricked" (and
    a 4 P card to t18);
  - Pilar: nobody has found hers yet; the frog on the University of Salamanca's façade is our guess.
Eggs and gifts never score by themselves (RULES, Scoring); a gifted card can still be sold to a team, and badges
show on the public leaderboard. Each line goes out at most once for the whole game, next to a real price.
"""
from __future__ import annotations

LINES = {
    "abuela": [
        "¡Y feliz santo, Carmen! Aquí seguimos con el «sile, nole, repe, me falta», y el chotis se baila sobre una baldosa.",
        "Por cierto, Carmen: no hay domingo sin un cocido con sus tres vuelcos, como el de su madre.",
    ],
    "chato": ["Luego un vermut en la Plaza Mayor, con caña, que eso es Madrid."],
    "picaros": ["Ojo, amigos, que conozco el timo de la estampita: Lazarillo y Rinconete eran de mi barrio."],
    "pilar": ["Doña Pilar, ¿encontró usted la rana en la fachada de la Universidad de Salamanca? Dicen que trae suerte."],
}


def line(state: dict, dealer: str) -> str:
    """The next unsaid line for this dealer (and mark it said), or ''."""
    said = state.setdefault("eggs_said", [])
    for i, text in enumerate(LINES.get(dealer, [])):
        key = f"{dealer}:{i}"
        if key not in said:
            said.append(key)
            return text
    return ""
