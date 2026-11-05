"""Dessine les badges des trophées (un JPEG par trophée et par palier) dans donnees/trophees/. À lancer sur un Mac une fois pour toutes :
il emprunte la police d'emojis d'Apple, que le VPS n'a pas ; les images produites sont commitées et c'est tout ce dont le bot a besoin.
    .venv/bin/python scripts/generer_badges.py [code ...]        (sans argument : tous les badges)"""
import colorsys
import random
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentski.trophees import CATALOGUE, VITRINE  # noqa: E402

SORTIE = Path(__file__).resolve().parent.parent / "donnees" / "trophees"
T = 720            # côté final
K = 2              # suréchantillonnage : on dessine en double puis on réduit
FUTURA, AVENIR, EMOJI = "/System/Library/Fonts/Supplemental/Futura.ttc", "/System/Library/Fonts/Avenir Next.ttc", "/System/Library/Fonts/Apple Color Emoji.ttc"

FAMILLES = {"chutes": 8, "presence": 205, "serie": 275, "jeux": 330, "surprises": 40, "musique": 150, "montre": 190, "lieux": 120}
ICONES = {"premier_sang": ("chutes", "🩸"), "pingouin": ("chutes", "🐧"), "phenix": ("chutes", "🔥"), "chute_verte": ("chutes", "🟢"), "double": ("chutes", "✌️"),
          "weekend_blanc": ("chutes", "❄️"), "incassable": ("chutes", "🛡️"), "tombe_premier_jour": ("chutes", "🤕"),
          "premiere_trace": ("presence", "👣"), "fidele": ("presence", "🤝"), "infatigable": ("presence", "🔋"), "jamais_deux": ("presence", "🔁"),
          "revenant": ("presence", "👻"), "invite_honneur": ("presence", "🎟️"), "nouvel_an": ("presence", "🎆"),
          "cameraman": ("serie", "🎥"), "vedette": ("serie", "⭐"), "poete": ("serie", "✒️"), "idealiste": ("serie", "💡"),
          "parieur": ("jeux", "🎰"), "pari_tenu": ("jeux", "🤞"), "rendez_vous": ("lieux", "📍"), "meteorologue": ("lieux", "🌦️"),
          "premier_defi": ("jeux", "🏁"), "gage": ("jeux", "🎭"), "chef_etoile": ("jeux", "🍳"), "dj_resident": ("musique", "🎧"),
          "debatteur": ("jeux", "🗣️"), "avocat_diable": ("jeux", "⚖️"), "coupable": ("jeux", "🔨"), "madame_irma": ("jeux", "🔮"),
          "archiviste": ("surprises", "🗄️"), "cible_favorite": ("surprises", "🎯"), "chambreur": ("surprises", "😏"),
          "melomane": ("musique", "🎵"), "mono_titre": ("musique", "🔂"), "kilometre_vertical": ("montre", "📐"), "everest": ("montre", "🏔️"),
          "marathonien": ("montre", "🏃"), "fusee": ("montre", "🚀"), "leve_tot": ("montre", "🌅"), "dernier_remonte": ("montre", "🌇"),
          "grand_tour": ("lieux", "🗺️"), "explorateur": ("lieux", "🧭"), "la_cascade": ("lieux", "💧"), "tout_en_haut": ("lieux", "⛰️"),
          "vitrine_pleine": ("presence", "🏆")}
PALIERS = {0: ((246, 209, 115), (170, 118, 36), "TROPHÉE"), 1: ((222, 148, 82), (125, 70, 30), "BRONZE"),
           2: ((232, 237, 243), (126, 138, 152), "ARGENT"), 3: ((250, 214, 112), (176, 120, 30), "OR")}


def police(chemin: str, taille: int, index: int = 0) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(chemin, taille * K, index=index)


def degrade(taille: tuple[int, int], haut: tuple, bas: tuple) -> Image.Image:
    masque = Image.linear_gradient("L").resize(taille)
    return ImageOps.colorize(masque, black=haut, white=bas).convert("RGBA")


def ecu(cx: float, cy: float, largeur: float, hauteur: float) -> list[tuple[float, float]]:
    """Le contour d'un écu : un haut légèrement bombé, des flancs droits puis une pointe arrondie."""
    pts, g, d, h = [], cx - largeur / 2, cx + largeur / 2, cy - hauteur / 2

    def bez(p0, p1, p2, n=24):
        return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0], (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in [i / n for i in range(n + 1)]]
    pts += bez((g, h + hauteur * .1), (cx, h - hauteur * .06), (d, h + hauteur * .1))
    pts += [(d, h + hauteur * .52)]
    pts += bez((d, h + hauteur * .52), (d - largeur * .04, h + hauteur * .84), (cx, h + hauteur))
    pts += bez((cx, h + hauteur), (g + largeur * .04, h + hauteur * .84), (g, h + hauteur * .52))
    return pts


def homothetie(pts, cx, cy, f):
    return [(cx + (x - cx) * f, cy + (y - cy) * f) for x, y in pts]


def masque_de(pts, taille):
    m = Image.new("L", taille, 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m


def etincelle(d: ImageDraw.ImageDraw, x, y, r, couleur):
    d.polygon([(x, y - r), (x + r * .22, y - r * .22), (x + r, y), (x + r * .22, y + r * .22), (x, y + r), (x - r * .22, y + r * .22), (x - r, y), (x - r * .22, y - r * .22)], fill=couleur)


def badge(code: str, nom: str, description: str, seuil: int | None, niveau: int, verrouille: bool = False) -> Image.Image:
    N = T * K
    fam, emoji = ICONES[code]
    h = FAMILLES[fam]
    teinte = lambda s, l: tuple(int(c * 255) for c in colorsys.hls_to_rgb(h / 360, l, s))
    haut, bas = (teinte(.55, .60), teinte(.62, .30)) if not verrouille else ((92, 104, 118), (44, 54, 66))
    img = degrade((N, N), (12, 22, 33), (23, 40, 58))
    # halo derrière l'écu
    halo = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse((N * .16, N * .1, N * .84, N * .78), fill=teinte(.7, .45) + ((70,) if not verrouille else (18,)))
    img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(N * .06)))
    # étincelles
    alea, d = random.Random(code), ImageDraw.Draw(img)
    if not verrouille:
        posees = 0
        while posees < 14:
            x, y = alea.uniform(.06, .94), alea.uniform(.06, .66)
            if .24 < x < .76 and .10 < y < .66:
                continue  # jamais derrière l'écu
            etincelle(d, x * N, y * N, alea.uniform(.008, .022) * N, (255, 255, 255, alea.randint(70, 200)))
            posees += 1
    cx, cy, w, hh = N / 2, N * .39, N * .40, N * .46
    contour = ecu(cx, cy, w, hh)
    rim_haut, rim_bas, etiquette = PALIERS[niveau] if not verrouille else ((120, 130, 142), (60, 70, 82), "")
    # ombre portée
    ombre = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(ombre).polygon([(x, y + N * .02) for x, y in contour], fill=(0, 0, 0, 150))
    img.alpha_composite(ombre.filter(ImageFilter.GaussianBlur(N * .015)))
    for pts, c1, c2 in ((contour, rim_haut, rim_bas), (homothetie(contour, cx, cy, .90), haut, bas)):
        couche = degrade((N, N), c1, c2)
        img.paste(couche, (0, 0), masque_de(pts, (N, N)))
    interieur = homothetie(contour, cx, cy, .90)
    # filet clair et reflet
    d = ImageDraw.Draw(img, "RGBA")
    d.line(homothetie(contour, cx, cy, .955) + [homothetie(contour, cx, cy, .955)[0]], fill=(255, 255, 255, 60), width=K * 2)
    reflet = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(reflet).ellipse((cx - w * .62, cy - hh * .95, cx + w * .62, cy + hh * .02), fill=(255, 255, 255, 46))
    clip = ImageChops.multiply(reflet.split()[3], masque_de(interieur, (N, N)))
    reflet.putalpha(clip)
    img.alpha_composite(reflet)
    # emoji (ou point d'interrogation si verrouillé)
    if verrouille:
        f = police(FUTURA, 190, 2)
        d.text((cx, cy), "?", font=f, fill=(190, 200, 212, 150), anchor="mm")
    else:
        carre = Image.new("RGBA", (260, 260), (0, 0, 0, 0))
        ImageDraw.Draw(carre).text((20, 20), emoji, font=ImageFont.truetype(EMOJI, 160), embedded_color=True)
        carre = carre.crop(carre.getbbox())  # recentré sur ce qui est vraiment dessiné
        taille_e = int(N * .27)
        echelle = taille_e / max(carre.size)
        icone = carre.resize((max(1, int(carre.width * echelle)), max(1, int(carre.height * echelle))), Image.LANCZOS)
        ombre_e = Image.new("RGBA", (N, N), (0, 0, 0, 0))
        ombre_e.paste((0, 0, 0, 110), (int(cx - icone.width / 2), int(cy - icone.height / 2 + N * .012)), icone.split()[3])
        img.alpha_composite(ombre_e.filter(ImageFilter.GaussianBlur(N * .008)))
        img.alpha_composite(icone, (int(cx - icone.width / 2), int(cy - icone.height / 2 - N * .004)))
    # bandeau avec le nom
    by, bh = N * .70, N * .105
    d.polygon([(N * .12, by), (N * .88, by), (N * .84, by + bh / 2), (N * .88, by + bh), (N * .12, by + bh), (N * .16, by + bh / 2)], fill=(8, 15, 24, 235), outline=rim_haut + (255,), width=K * 3)
    titre = nom.upper() if not verrouille else "? ? ?"
    taille_t = 46
    while police(FUTURA, taille_t, 2).getlength(titre) > N * .66 and taille_t > 20:
        taille_t -= 2
    d.text((N / 2, by + bh / 2), titre, font=police(FUTURA, taille_t, 2), fill=(250, 252, 255, 255) if not verrouille else (150, 160, 172, 255), anchor="mm")
    # étiquette de palier et description
    if etiquette:
        sous = f"{etiquette} · {seuil}" if seuil else etiquette
        d.text((N / 2, N * .043), "TROPHÉE DÉBLOQUÉ", font=police(AVENIR, 21, 2), fill=(170, 186, 204, 255), anchor="mm")
        d.text((N / 2, by + bh + N * .045), sous, font=police(AVENIR, 27, 0), fill=rim_haut + (255,), anchor="mm")
    corps = "\n".join(textwrap.wrap(description if not verrouille else "Trophée caché : continue de skier.", 38))
    d.multiline_text((N / 2, by + bh + N * (.115 if etiquette else .08)), corps, font=police(AVENIR, 24, 5), fill=(190, 202, 216, 255), anchor="ma", align="center", spacing=8 * K)
    return img.convert("RGB").resize((T, T), Image.LANCZOS)


def main(codes: list[str]) -> None:
    SORTIE.mkdir(parents=True, exist_ok=True)
    elements = [(c, n, dsc, s) for c, n, dsc, s, _ in CATALOGUE] + [("vitrine_pleine", "Vitrine pleine", "trophées différents débloqués", VITRINE)]
    n = 0
    for code, nom, description, seuils in elements:
        if codes and code not in codes:
            continue
        niveaux = range(1, len(seuils) + 1) if seuils else [0]
        for niveau in niveaux:
            seuil = seuils[niveau - 1] if seuils else None
            img = badge(code, nom, f"{seuil:,} {description}".replace(",", " ") if seuils else description, seuil, niveau)
            img.save(SORTIE / f"{code}_{niveau or 1}.jpg", quality=88, optimize=True)
            n += 1
    if not codes:
        badge("premier_sang", "", "", None, 0, verrouille=True).save(SORTIE / "verrou.jpg", quality=88, optimize=True)
    print(f"{n} badge(s) dans {SORTIE}")


if __name__ == "__main__":
    main(sys.argv[1:])
