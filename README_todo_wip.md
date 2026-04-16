# README todo wip

source .venv/bin/activate

- il parser va modificato perché deve accorgersi dei vaccini che scavallano la pagina
- lo stage 5 dà problemi di visualizzazione perché prende le età e non le medie (ancora...)
- meno file inutili salvati perché non me ne frega ninete
- vaccini completamente certicalizzati? boh vorrei farli almeno incrociati


Stato consolidato dell'ultimo giro:
- stage1 vaccini gestisce meglio le righe spezzate delle dosi e usa `document_id = pdf_stem` come chiave stabile
- stage3 tiene `documenti_importati` per saltare i documenti gia caricati
- stage4 usa di nuovo il DFG standard di PM4Py, con una patch minima per mostrare anche i casi a evento singolo
- stage5 in vista aggregata mostra traiettorie medie per eta e non sovrappone tutti i pazienti


## focus attuale

Stiamo lavorando sul ramo vaccini end-to-end:
- `test_stage1.py` per lettura e interpretazione
- `test_stage3.py` per database verticale vaccini + anagrafiche
- `test_stage4.py` per event log e process mining
- `test_stage5.py` come interfaccia locale principale

In questo momento la priorita' non e' React.
La UI di riferimento resta `test_stage5.py`, tutta locale, senza dipendenze web esterne.
React resta una possibilita' futura, ma solo quando la forma dei dati e delle viste sara' piu stabile.

Per ora non stiamo ancora aggiornando i sorgenti veri sotto `src/`.
Le decisioni consolidate restano in `statolavori.txt`.

## task 1 - vaccini end-to-end

Gia' fatto:
- rinominato `smoke_test_stages_1_to_5.py` in `test_stage1.py`
- rinominato `test_only_llm.py` in `test_stage2.py`
- fermato concettualmente `stage1` prima della chiamata al modello
- diviso `stage1` in 5 fasi leggibili
- chiarita la distinzione tra logica comune e logica vaccini
- standardizzati gli artefatti base di `stage1`
- spostati `layout_*` e `reader_*` tra gli artefatti di debug
- raffinata l'anagrafica vaccini nel JSON interpretato
- introdotto `document_snapshot_date` dal filename del certificato
- creato `aggregated database/vaccini.sqlite`
- creato `aggregated database/anagrafiche_pazienti.sqlite`
- aggiunto `sessione_id` ai vaccini
- costruiti gli output stage 4:
  - log progressione
  - validation report
  - summary JSON
  - XES
- costruita `test_stage5.py` come dashboard locale per il mining vaccini

Ancora da fare sul ramo vaccini:
- rifinire ancora la resa del grafo verticale di `test_stage5.py` quando serve
- chiarire sempre meglio il confine tra vista di process mining pura e vista clinica aggregata
- capire se tenere o meno il branch LLM per i vaccini come fallback o confronto
- decidere quando considerare stabile il flusso vaccini e iniziare il porting verso `src/`

## task 2 - stage 1 non vaccini

- testare anche altri tipi di documenti:
  - `Documento_sanitario_*`
  - `Ricetta_*`
  - `Riepilogo_*` solo in modo minimo
- capire se i tag iniziali dei documenti non vaccinali sono gia' abbastanza utili oppure no
- definire la forma minima di `interpreted_text.json` per i documenti non vaccinali
- chiarire quali metadati si riescono a estrarre in modo robusto senza LLM

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

## task 4 - database e modelli intermedi

- progettare meglio:
  - json paziente cumulativo
  - `document_event.json`
  - `document_findings.json`
- decidere come estendere `aggregated database/` a:
  - documenti sanitari
  - ricette
  - eventuale database aggregato finale
- chiarire come collegare artefatti, eventi e verticali cliniche senza mischiare livelli diversi

## task 5 - interfaccia locale

Gia' deciso:
- `test_stage5.py` e' la UI locale di riferimento per ora
- niente React come priorita' immediata
- niente dipendenze internet o servizi esterni

Ancora da fare:
- continuare a migliorare il grafo verticale della progressione
- decidere quali controlli tenere stabili e quali sono solo sperimentali
- capire se aggiungere una separazione piu netta tra vista di mining e vista clinica
- capire se aggiungere altre viste locali oltre alla progressione vaccini
- valutare in seguito se conviene trasformare la UI locale in uno stage 5 piu strutturato

## questioni aperte ma non bloccanti

- come modellare l'evoluzione dell'anagrafica paziente nel tempo
- come collegare `document_event.json` e `document_findings.json` al database
- come aggiornare `document_artifacts` con i nuovi artefatti di `stage1` e `stage2`
- quando passare dai test ai sorgenti veri sotto `src/`
- quando valutare vLLM al posto di Ollama
- fare un database unificato di tutte le categorie di `Documento_sanitario_*` per migliorare il tagging
- se React servira' davvero in futuro o se la UI locale restera' sufficiente piu a lungo del previsto

## vincoli attivi

- non toccare ancora i sorgenti veri sotto `src/`
- `stage1` deve usare l'LLM il meno possibile
- i vaccini hanno una logica dedicata
- il prompt e' output di `stage1`
- `stage2` deve solo eseguire e registrare
- il database e gli artefatti sono incrementali: niente cleanup automatico
- la struttura minima di database gia' definita in `statolavori.txt` non va persa
- la UI deve restare tutta locale, senza internet vero

## riferimenti

- `README.md` = panoramica generale del progetto
- `statolavori.txt` = decisioni consolidate e struttura minima database
- `README_todo_wip.md` = task aperte e prossime mosse
