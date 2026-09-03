# Learning Case: V2-STABILITY-002-window-startup-timeout

## Metadata
MODEL: claude-opus-5
AGENT: fable-lead
START_SHA: d2750fc
END_SHA: HEAD+1
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: tool:pytest:command-center/tests/test_ux2_desktop.py
CONFIDENCE: 0.9
TAGS: {"domain": "desktop_stability", "bug_class": "wrong_timeout_scope", "component": "bcc.desktop", "severity": "HIGH"}
FINDINGS: V2-STABILITY-002

## Task
Таймаут старта закрывал исправно работающее окно приложения

## Symptom
Окно BOSSMAN закрывалось само через заданное --window-timeout (или BCC_APP_STARTUP_TIMEOUT) секунд, даже когда владелец им пользовался и всё работало. Со стороны выглядело как самопроизвольное закрытие дашборда.

## Reproduction
- launch_window(..., timeout=T) с браузером, живущим дольше T: процесс получал terminate, затем kill, и функция возвращала 124
- тест test_working_window_outliving_startup_timeout_is_never_killed воспроизводит это процессом-заглушкой, живущим 1.5 c при timeout=0.3

## Evidence
- код до фикса: proc.wait(timeout=timeout) в try, а в except TimeoutExpired — proc.terminate(), proc.wait(5), proc.kill(), return 124
- после фикса: тот же сценарий возвращает код 0 и занимает всё время жизни окна, то есть окно никто не трогал

## Hypotheses considered
- таймаут применялся к сроку жизни окна вместо срока старта (подтвердилось)
- браузер падал сам (отвергнуто: код 124 ставила наша ветка kill)

## Rejected hypotheses + why
- просто увеличить таймаут — сдвигает момент убийства, а не убирает его
- убрать таймаут совсем — тогда неудачный старт ждёт вечно и владелец опять без ответа
- определять пустое окно по живости процесса — живость не отличает пустое окно от рабочего

## Root cause
Значение --window-timeout передавалось прямо в proc.wait(timeout=...), то есть ограничивало ВСЁ время жизни окна. По его истечении ветка обработки делала terminate и kill. Таймаут старта тем самым стал таймером закрытия.

## Relevant code paths
- command-center/bcc/desktop.py:launch_window
- command-center/bcc/desktop.py:STARTUP_FAILED_CODE

## Fix strategy
Таймаут стал наблюдением за стартом: окно, пережившее его, ждётся без предела и закрывается только владельцем; окно, умершее раньше срока, возвращает свой код выхода. Завершать окно принудительно разрешено лишь когда вызывающий передал проверку готовности ready() и она не подтвердилась — тогда terminate, затем kill и код 124.

## Alternatives considered
- оставить kill, но с большим значением по умолчанию
- проверять готовность через CDP (требует отладочного порта в бою)

## Why this fix was chosen
Убирает саму возможность закрыть рабочее окно по времени, сохраняя диагностику неудачного старта; принудительное завершение осталось только там, где неготовность действительно доказана.

## Files changed
- command-center/bcc/desktop.py
- command-center/tests/test_ux2_desktop.py

## Tests added
- test_working_window_outliving_startup_timeout_is_never_killed
- test_window_that_dies_early_returns_its_own_exit_code
- test_unconfirmed_window_is_closed_only_when_readiness_is_checkable
- test_confirmed_window_waits_for_the_owner_not_for_the_timeout

## Original reproduction after fix
окно, живущее дольше таймаута, получало terminate/kill и код 124

## Adversarial variants
- окно живёт дольше таймаута — не убивается
- окно умирает раньше срока — код выхода доходит как есть
- готовность проверяема и не подтверждена — аккуратное завершение и код 124
- готовность подтверждена — предел снимается, ждём владельца
- таймаут не задан вовсе — поведение прежнее, ожидание без предела

## Regression
tests/test_ux2_desktop.py 47 passed (было 43)

## Fresh external verification
pytest с настоящими дочерними процессами, без подмены Popen

## Failed approaches / recovery lessons
- Таймаут старта и таймаут жизни — разные величины; смешивать их нельзя

## Generalizable lessons
- Ограничение, поставленное на старт, не должно применяться к последующей работе
- Убивать процесс можно, только когда его неготовность доказана, а не когда истекло время
- Живость процесса не является доказательством готовности интерфейса

## Teach local model
- Распознать: timeout передаётся в wait(), а по нему делается terminate/kill
- Предпочесть: таймаут как окно наблюдения за стартом, дальше ожидание без предела
- Проверять: процесс, живущий дольше таймаута, обязан вернуть свой код, а не 124

## Limitations / follow-up
- случай «окно живо, но пустое» одним таймаутом не отличается от рабочего; без ready() он сюда не приходит и разбирается диагностикой пустого окна и сторожем
- на настоящей Windows двойным кликом не проверялось: здесь нет ни дисплея, ни Windows
