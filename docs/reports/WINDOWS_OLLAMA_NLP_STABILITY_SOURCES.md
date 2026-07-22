# Windows / Ollama / NLP stability — źródła implementacji

Zmiana opiera się na źródłach pierwotnych i oficjalnej dokumentacji:

- Python `subprocess`: `CREATE_NO_WINDOW`, `STARTUPINFO`, `SW_HIDE` — https://docs.python.org/3.14/library/subprocess.html
- Microsoft Win32 Process Creation Flags: semantyka `CREATE_NO_WINDOW`, `CREATE_NEW_CONSOLE`, `DETACHED_PROCESS`, `CREATE_NEW_PROCESS_GROUP` — https://learn.microsoft.com/windows/win32/procthread/process-creation-flags
- Microsoft Sysinternals Process Monitor: ścieżka obrazu, command line, proces rodzic i drzewo procesów — https://learn.microsoft.com/sysinternals/downloads/procmon
- Ollama `POST /api/chat`: `model`, `messages`, `done`, `done_reason`, metryki i `keep_alive` — https://docs.ollama.com/api/chat
- Ollama `GET /api/tags`: lista dostępnych modeli — https://docs.ollama.com/api/tags
- fastText language identification i publikacje referencyjne — https://fasttext.cc/docs/en/language-identification.html
- Jauhiainen et al., *Automatic Language Identification in Texts: A Survey* — https://arxiv.org/abs/1804.08186
- Morfeusz 2 / SGJP: analiza fleksyjna języka polskiego — https://morfeusz.sgjp.pl/doc/about/

## Granica implementacji

Core nie dołącza ciężkiego modelu LID ani zewnętrznych baz. Nowy guard jest konserwatywnym filtrem kandydatów odpowiedzi: odrzuca wyraźnie angielski tekst przy wymaganym polskim, ale akceptuje krótkie i techniczne teksty o niejednoznacznym języku. Pełny model fastText pozostaje opcjonalnym zasobem zewnętrznym po osobnej kontroli licencji.
