# README comandi

Questa pagina serve a ricordare in modo pratico quali comandi esistono davvero oggi e quando usarli.

Tutti i comandi partono da:

```bash
python main.py ...
```

Se usi il venv locale, puoi anche fare:

```bash
venv/bin/python main.py ...
```

## idea semplice

Per ora la CLI fa soprattutto 3 cose:

- controlla lo stato del progetto
- guarda i PDF disponibili
- estrae il testo grezzo da un PDF

Non costruisce ancora:

- il database finale
- l'estrazione LLM completa in JSON
- il process mining

Quindi, se pensi "voglio il DB solo per i vaccini", oggi la risposta giusta è:

- prima isolo solo i documenti vaccinali
- poi leggo o processo solo quelli
- il comando che costruisce davvero il DB vaccini arriverà più avanti

## comandi disponibili adesso

### 1. vedere la configurazione attiva

```bash
python main.py health
```

Serve per vedere dove il progetto sta cercando:

- `data/`
- `data/raw/`
- `artifacts/`
- URL di Ollama
- nome del modello configurato

### 2. controllare Ollama

```bash
python main.py ollama-health
```

Serve per verificare se Ollama risponde e se vede il modello configurato.

### 3. vedere tutti i PDF del dataset

```bash
python main.py list-pdfs
```

### 4. vedere solo una famiglia di documenti

Esempio: solo vaccini

```bash
python main.py list-pdfs --family vaccination_certificate
```

Famiglie disponibili oggi:

- `vaccination_certificate`
- `summary`
- `clinical_document`
- `prescription`
- `unknown`

### 5. capire di che tipo è un PDF

```bash
python main.py classify data/raw/person001/NOMEFILE.pdf
```

Questo comando guarda il filename e prova a dire se il documento è:

- vaccino
- riepilogo
- documento sanitario
- ricetta

### 6. estrarre il testo da un PDF

```bash
python main.py extract-text data/raw/person001/NOMEFILE.pdf
```

Se vuoi un'anteprima più lunga:

```bash
python main.py extract-text data/raw/person001/NOMEFILE.pdf --preview-chars 1500
```

## scorciatoia dedicata ai vaccini

Per togliere un po' di confusione, esiste anche un comando dedicato `vaccini`.

### vedere solo i PDF vaccinali

```bash
python main.py vaccini list
```

Questo comando è equivalente a:

```bash
python main.py list-pdfs --family vaccination_certificate
```

### estrarre il testo da un certificato vaccinale

```bash
python main.py vaccini extract-text data/raw/person001/CertificatoVaccinale_LMNLCU02E15D918M_20260303104746.pdf
```

Con anteprima più lunga:

```bash
python main.py vaccini extract-text data/raw/person001/CertificatoVaccinale_LMNLCU02E15D918M_20260303104746.pdf --preview-chars 1500
```

## se vuoi lavorare solo sui vaccini

Oggi il flusso più sensato è questo:

1. vedere i documenti vaccinali

```bash
python main.py vaccini list
```

2. prendere il path del certificato vaccinale

3. estrarre il testo di quel documento

```bash
python main.py vaccini extract-text data/raw/person001/CertificatoVaccinale_LMNLCU02E15D918M_20260303104746.pdf
```

Quindi sì: non devi processare tutto per guardare solo la parte vaccini.

## cosa non esiste ancora

Questi comandi non esistono ancora:

- un comando che manda il PDF al modello e restituisce JSON validato
- un comando che costruisce il database finale
- un comando che costruisce un database solo vaccini
- un comando di process mining

## come pensare al futuro comando vaccini

La tua idea è sensata:

- prima si isolano gli eventi/documenti vaccinali
- poi da quelli si costruisce la parte specifica vaccini del database

Quando arriveremo a quella fase, il posto naturale sarà probabilmente un comando tipo:

```bash
python main.py vaccini build-db
```

o qualcosa di simile.

Per adesso però `vaccini` serve come scorciatoia per lavorare solo sul sottoinsieme vaccinale.
