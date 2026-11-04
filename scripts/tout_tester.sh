#!/bin/sh
# Lance tous les tests ; s'arrête en erreur si l'un d'eux sort en erreur (et pas seulement s'il affiche « ÉCHEC »).
cd "$(dirname "$0")/.." || exit 1
statut=0
for t in scripts/test_*.py scripts/simulation_rappels.py; do
  sortie=$(.venv/bin/python "$t" 2>&1); code=$?
  if [ $code -ne 0 ]; then
    echo "ERREUR  $t"; echo "$sortie" | tail -8; statut=1
  else
    echo "ok      $t ($(echo "$sortie" | grep -c '^OK') vérifications)"
  fi
done
exit $statut
