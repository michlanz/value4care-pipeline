# value4care-pipeline

## pipeline dai pdf dei fascicoli fino a qualcosa che può essere usato dal process mining

## struttura del progetto

### root del repository

- `main.py`
  launcher principale del progetto. Per ora serve soprattutto ad avviare la CLI.
- `README.md`
  panoramica generale del progetto e della struttura del codice.
- `README_comandi.md`
  guida pratica ai comandi da terminale disponibili oggi.
- `statolavori.txt`
  diario tecnico e roadmap aggiornata del progetto.
- `data/`
  dataset di lavoro attivo.
- `data.zip`
  copia di backup del dataset.
- `requirements/`
  dipendenze Python del progetto.
- `tests/`
  test minimi sulla struttura iniziale.

### cartella `src/`

- `clinical.py`
  contiene i concetti clinici comuni del progetto: documento, diagnosi, evento clinico, care thread.
- `config.py`
  raccoglie configurazione e percorsi principali del progetto.

### pacchetti principali in `src/`

- `pdf_reading/`
  selezione dei PDF, classificazione iniziale dal filename, estrazione del testo con `pdfplumber`.
- `llm_runtime/`
  comunicazione con Ollama, prompt e parsing delle risposte del modello.
- `database/`
  struttura logica della persistenza, tabelle previste e percorsi degli artefatti.
- `mining/`
  trasformazione degli eventi clinici in event log e primi filtri utili al process mining.
- `interface/`
  punti di ingresso del progetto: oggi CLI, più avanti anche API o interfaccia interattiva.

## cos'è la CLI

CLI significa `Command Line Interface`, cioè interfaccia a riga di comando.

In pratica:

- invece di cliccare bottoni su una schermata
- scrivi un comando nel terminale
- il programma esegue quell'azione e ti mostra il risultato

Nel nostro progetto, per esempio, la CLI è la parte che permette di fare cose come:

- controllare la configurazione
- elencare i PDF
- classificare un documento dal filename
- estrarre il testo da un PDF

Quindi:

- `main.py` è il launcher
- `src/interface/cli.py` contiene i comandi da terminale

## cos'è un care_thread

`care_thread` è il contenitore logico che raggruppa documenti ed eventi che fanno parte dello stesso filo clinico.

Esempi:

- una frattura, con visita, radiografia, referto e ricette collegate
- una gastroscopia, con prescrizione, esame e referto finale
- un percorso più lungo, nato da sintomi continui o da una diagnosi emersa nel tempo

Serve perché spesso i collegamenti clinici non sono solo tra due documenti singoli, ma tra più documenti ed eventi che appartengono allo stesso percorso.

## ordine consigliato per leggere il codice

Se vuoi orientarti senza perderti, conviene aprire i file in questo ordine:

1. `main.py`
2. `src/interface/cli.py`
3. `src/config.py`
4. `src/clinical.py`
5. `src/pdf_reading/`
6. `src/llm_runtime/`
7. `src/database/`
8. `src/mining/`

## idea generale dell'architettura

La pipeline è pensata così:

PDF -> lettura testo -> interpretazione LLM -> validazione backend -> strutture dati -> event log -> process mining

Separazioni importanti:

- `pdf_reading` legge i documenti ma non interpreta clinicamente il contenuto
- `llm_runtime` interpreta il contenuto ma non salva direttamente nel database
- `database` si occupa della persistenza, non della lettura dei PDF
- `mining` lavora su eventi già strutturati, non sui PDF raw

## esempio utile da terminale

Per vedere solo i PDF vaccinali senza passare da tutto il dataset:

```bash
python main.py list-pdfs --family vaccination_certificate
```

Per estrarre il testo da uno specifico certificato vaccinale:

```bash
python main.py extract-text data/raw/person001/CertificatoVaccinale_LMNLCU02E15D918M_20260303104746.pdf
```
