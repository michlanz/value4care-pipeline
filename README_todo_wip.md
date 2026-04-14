# README todo wip

## focus attuale

Stiamo chiudendo prima il flusso vaccini.
In questo momento il focus non e' piu' il refactor base di `stage1`, ma:
- guardare il prompt prodotto da `stage1`
- fare test locali su vaccini
- rifinire il comportamento prima di passare agli altri tipi di documento

Per ora non stiamo ancora aggiornando i sorgenti veri sotto `src/`.
Le decisioni consolidate restano in `statolavori.txt`.

## task 1 - rifondare stage1

Gia' fatto:
- rinominato `smoke_test_stages_1_to_5.py` in `test_stage1.py`
- rinominato `test_only_llm.py` in `test_stage2.py`
- fermato concettualmente `stage1` prima della chiamata al modello
- diviso `stage1` in 5 fasi leggibili
- chiarita la distinzione tra logica comune e logica vaccini
- standardizzati gli artefatti base di `stage1`
- spostati `layout_*` e `reader_*` tra gli artefatti di debug

Ancora da fare su task 1:
- per adesso la pipeline si concentra solo sui vaccini, senza valutare gli altri tipi di documenti
- migliorare il prompt prodotto da `stage1`, che e' il vero output verso `stage2`
- rifinire l'estrazione anagrafica e dei metadati base nel caso vaccini
- verificare che `interpreted_text.txt` e `interpreted_text.json` siano davvero chiari e utili
- testare anche altri tipi di documenti:
  - `Documento_sanitario_*`
  - `Ricetta_*`
  - `Riepilogo_*` solo in modo minimo
- capire se i tag iniziali dei documenti non vaccinali sono gia' abbastanza utili oppure no

## task 2 - flusso vaccini e prompt

- fare prove locali sul prompt vaccini partendo dall'output di `test_stage1.py`
- capire come deve essere fatto il prompt prima di rifinirlo nel codice
- usare `test_stage2.py` come runner separato per i test LLM
- confrontare prompt diversi sullo stesso documento vaccinale, se utile
- tenere tracciati prompt e risposte come artefatti

## task 3 - parser e classificazione documentale

- mantenere le 4 famiglie principali:
  - vaccini
  - documenti sanitari
  - ricette
  - riepilogo
- assumere che probabilmente tutti i tipi di documento avranno un parser dedicato
- tenere il parser vaccini come primo parser dedicato forte
- aggiungere un parser ricette leggero
- per `Documento_sanitario_*` partire da:
  - tagging keyword-based
  - sottocategorie progressive
  - catalogo estensibile nel tempo
- non trattare il `riepilogo` come priorita' attuale

## task 4 - metadati base e json intermedi

- capire quali campi possiamo estrarre bene gia' in `stage1`
- minimo candidato:
  - nome e cognome
  - codice fiscale
  - data di nascita
  - residenza/indirizzo se presente
  - date tipizzate del documento
  - famiglia documento
  - sottocategoria/tag
- progettare meglio:
  - json paziente cumulativo
  - `document_event.json`
  - `document_findings.json`

## questioni aperte ma non bloccanti

- come modellare l'evoluzione dell'anagrafica paziente nel tempo
- come collegare `document_event.json` e `document_findings.json` al database
- come aggiornare `document_artifacts` con i nuovi artefatti di `stage1` e `stage2`
- quando passare dai test ai sorgenti veri sotto `src/`
- quando valutare vLLM al posto di Ollama
- fare un database unificato di tutte le categorie di `Documento_sanitario_*` per migliorare il tagging

## vincoli attivi

- non toccare ancora i sorgenti veri sotto `src/`
- `stage1` deve usare l'LLM il meno possibile
- i vaccini hanno una logica dedicata
- il prompt e' output di `stage1`
- `stage2` deve solo eseguire e registrare
- la struttura minima di database gia' definita in `statolavori.txt` non va persa

## riferimenti

- `README.md` = panoramica generale del progetto
- `statolavori.txt` = decisioni consolidate e struttura minima database
- `README_todo_wip.md` = task aperte e prossime mosse
