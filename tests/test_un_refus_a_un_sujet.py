"""
Un refus, c'est une négation dont le SUJET est la source — pas n'importe quelle négation.

CE QUI A ÉTÉ MESURÉ, le 02/09/2026
-----------------------------------
En listant les marqueurs qui décident à eux seuls, sur toutes les réponses archivées,
quatre se révèlent ambigus. Les phrases sont relevées telles quelles :

  « sauf si leur objet **ne permet pas** l'identification de prestations distinctes »
        → c'est L2113-10 cité mot pour mot, pas un refus.

  « Si le montant des sommes dues **ne permet pas** de prélever la retenue, le titulaire
    doit constituer une garantie à première demande »
        → une condition juridique, dans une réponse qui donne le taux de 5 %.

  « L'article 15 **ne contient pas** de clause explicite sur ce point, **mais** la règle
    générale de calcul s'applique »
        → une nuance, dans une réponse qui répond.

  « Le code **ne fixe pas un** taux minimum obligatoire. Le pouvoir adjudicateur
    détermine le taux appliqué »
        → une précision, dans une réponse qui donne le taux maximum.

LE BIAIS EST DIFFÉRENTIEL, ce qui est le pire cas pour une comparaison. Le cœur
préfixe toutes ses réponses de « Cette information ne figure pas dans les passages
fournis » : il déclenche sur une formule non ambiguë. Le pipeline rédige librement et
cite le droit — il tombe donc plus souvent dans le piège, dans le sens qui l'avantage.

LA RÈGLE
---------
Ce qui distingue les deux, c'est le sujet de la négation :

  « **les passages fournis** ne contiennent pas la liste »   → refus
  « **l'article 15** ne contient pas de clause »             → contenu

Les verbes ambigus exigent donc un sujet désignant la source. Les formules qui portent
leur sujet en elles — « je ne dispose pas », « cette information ne figure pas » —
restent reconnues telles quelles.
"""
from colaig.rag.garde_fou_reponse import _est_un_refus

# ── Ce qui DOIT être reconnu comme refus ────────────────────────────────────

REFUS = [
    "Les passages fournis ne contiennent pas la liste actuelle des services.",
    "Les documents fournis ne précisent pas le seuil réglementaire.",
    "Cette information ne figure pas dans les passages fournis.",
    "Je ne dispose pas de ces informations dans les documents fournis.",
    "Le corpus ne contient aucune jurisprudence.",
    # Réfutation de prémisse — 7 des 22 cas négatifs.
    "Le Code de la commande publique ne fixe pas de limite légale générale au nombre "
    "maximal de lots.",
    "Le code n'impose aucun formalisme de négociation en procédure adaptée.",
    "Il ne fixe aucun nombre de jours minimum spécifique pour ce cas de figure.",
]

# ── Ce qui NE doit PAS l'être — phrases relevées dans les mesures ───────────

CONTENU = [
    "L2113-10 pose le principe selon lequel les marchés sont passés en lots séparés, "
    "sauf si leur objet ne permet pas l'identification de prestations distinctes.",
    "Si le montant des sommes dues au titulaire ne permet pas de procéder au "
    "prélèvement, celui-ci doit constituer une garantie à première demande.",
    "L'article 15 ne contient pas de clause explicite sur ce point, mais la règle "
    "générale de calcul des jours s'applique.",
    "Le code ne fixe pas un taux minimum obligatoire. Le pouvoir adjudicateur "
    "détermine le taux appliqué dans les documents particuliers.",
    "Le montant de la retenue de garantie ne peut excéder 5 % du montant initial, "
    "conformément à l'article R2191-33.",
]


def test_les_refus_authentiques_restent_reconnus():
    for texte in REFUS:
        assert _est_un_refus(texte), texte


def test_une_negation_du_droit_n_est_pas_un_refus():
    """Le cœur du correctif : ces cinq phrases répondent, elles ne refusent pas."""
    for texte in CONTENU:
        assert not _est_un_refus(texte), texte


def test_un_verbe_ambigu_compte_quand_son_sujet_est_la_source():
    """Le même verbe, les deux sens — c'est le sujet qui tranche."""
    assert _est_un_refus("Les passages fournis ne mentionnent pas ce taux.")
    assert not _est_un_refus("L'article L2112-4 ne mentionne pas cette restriction.")
