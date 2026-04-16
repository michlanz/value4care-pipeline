# value4care-pipeline

## pipeline offline dai pdf del fascicolo sanitario fino a database, process mining e visualizzazione locale

## stato sintetico attuale

Oggi la pipeline e' organizzata in 5 fasi concettuali:

1. `stage1` = lettura PDF, classificazione, interpretazione iniziale, metadati base, prompt output
2. `stage2` = runtime LLM locale quando serve
3. `stage3` = salvataggio su database e strutture persistenti
4. `stage4` = costruzione event log e process mining
5. `stage5` = visualizzazione locale dei risultati

Il ramo oggi piu' avanzato e verificato e' quello dei vaccini.
Per i vaccini abbiamo gia' un flusso end-to-end sperimentale che arriva fino alla visualizzazione locale.
Per gli altri tipi di documento la pipeline e' ancora in fase di estensione.

## stato pratico dei runner di test

In questa fase il lavoro principale vive nei runner `test_stage*`:

- `test_stage1.py`
  lettura e interpretazione locale del documento
- `test_stage2.py`
  runner separato per prove LLM locali
- `test_stage3.py`
  costruzione del database locale per i vaccini
- `test_stage4.py`
  costruzione degli event log e dei file di mining
- `test_stage5.py`
  dashboard locale per esplorare i risultati del process mining

Regola attuale di lavoro:
- prima si valida nei runner di test
- poi, solo quando una strategia e' abbastanza stabile, si porta dentro `src/`

## struttura del repository

### root del repository

- `main.py`
  launcher principale del progetto
- `README.md`
  panoramica generale del progetto
- `README_todo_wip.md`
  task aperte e focus del momento
- `statolavori.txt`
  stato tecnico consolidato e decisioni di progetto
- `test_stage1.py`
  stage 1 sperimentale locale
- `test_stage2.py`
  stage 2 sperimentale locale
- `test_stage3.py`
  stage 3 sperimentale locale
- `test_stage4.py`
  stage 4 sperimentale locale
- `test_stage5.py`
  stage 5 sperimentale locale
- `artifacts/`
  artefatti documentali generati dai test
- `aggregated database/`
  database locali e output di mining
- `data/`
  dataset di lavoro attivo
- `data.zip`
  copia di backup del dataset
- `requirements/`
  dipendenze Python del progetto
- `src/`
  sorgenti veri del progetto, ancora da riallineare pienamente alle soluzioni validate nei test

### cartella `src/`

- `clinical.py`
  concetti clinici comuni del progetto
- `config.py`
  configurazione e percorsi principali

### pacchetti principali in `src/`

- `stage1_pdf_reading/`
  lettura PDF e classificazione iniziale
- `stage2_llm_runtime/`
  runtime LLM locale
- `stage3_database/`
  persistenza e schema logico
- `stage4_mining/`
  trasformazione in event log e filtri mining
- `stage5_interface/`
  interfacce di accesso; oggi la UI locale di riferimento vive ancora in `test_stage5.py`

## cosa significa ogni fase oggi

### stage 1

Legge il PDF e costruisce una rappresentazione utile del documento.

Per i vaccini oggi comprende gia':
- estrazione testo
- interpretazione layout-aware
- anagrafica paziente
- date tipizzate
- contenuto clinico separato dai metadati anagrafici
- gestione robusta di dosi spezzate su piu righe e continuation lines nell header vaccinale
- `document_id` stabile uguale al nome del PDF, usato per organizzare e skippare gli artifact stage1
- prompt base come output verso stage 2

### stage 2

Esegue il modello locale solo quando serve.

Per i vaccini oggi non e' piu' il ramo principale:
- il parsing deterministico e' preferito
- il runner LLM resta utile per confronto o fallback

### stage 3

Salva i dati in strutture persistenti locali.

Per i vaccini oggi produce:
- `aggregated database/vaccini.sqlite`
- `aggregated database/anagrafiche_pazienti.sqlite`
- tracking documentale incrementale con `documenti_importati` per evitare reimport inutili in stage3

### stage 4

Costruisce gli event log e gli output per il process mining.

Per i vaccini oggi produce:
- `.xes`
- log json
- summary json
- validation report
- grafi/artefatti di mining
- DFG progressione con stile standard PM4Py e patch minima per mostrare anche i casi a evento singolo

### stage 5

Visualizza i risultati in locale.

Oggi la UI di riferimento e':
- `test_stage5.py`
- locale only
- senza React
- senza dipendenze web esterne
- con grafo interattivo SVG per la progressione vaccinale
- vista aggregata basata su eta medie di coorte, non su tutti i pazienti sovrapposti
- asse verticale in eta, non in data assoluta

## principi architetturali confermati

- tutto deve restare offline e privacy first
- l'LLM deve essere locale
- il database non deve essere confuso con gli artefatti tecnici
- il database e gli artefatti sono incrementali
- niente cleanup automatico dei file generati
- i vaccini hanno una logica dedicata
- bisogna usare l'LLM il meno possibile quando un parser deterministico e' sufficiente

## ordine consigliato per orientarsi oggi

Se vuoi capire il progetto nello stato reale attuale, conviene leggere in questo ordine:

1. `README.md`
2. `README_todo_wip.md`
3. `statolavori.txt`
4. `test_stage1.py`
5. `test_stage3.py`
6. `test_stage4.py`
7. `test_stage5.py`
8. `src/`

## comandi utili oggi

Attivare il venv:

```bash
source .venv/bin/activate
```

Lanciare il flusso vaccini sperimentale:

```bash
python test_stage1.py
python test_stage3.py
python test_stage4.py
python test_stage5.py
```

Poi aprire in locale:

```text
http://127.0.0.1:8050
```

## punto di attenzione attuale

La pipeline completa e' oggi davvero matura soprattutto sul caso vaccini.
Il prossimo passo robusto non e' aggiungere subito complessita' di UI o framework frontend, ma:
- aggiungere altri pazienti
- verificare che database e mining reggano su piu' casi
- estendere poi la stessa struttura agli altri tipi documentali
