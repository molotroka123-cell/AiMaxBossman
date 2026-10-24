# Foundation acceptance gates

PASS only if all:

1. economic North Star explicit;
2. vanity metrics rejected;
3. opportunity object defined;
4. evidence classes defined;
5. freshness policy defined;
6. contradiction handling defined;
7. score + confidence multiplier defined;
8. hard blockers defined;
9. stage gates defined;
10. payment proof before scale;
11. bypass requires written exception;
12. kill criteria defined;
13. pivot versioning defined;
14. unit economics defined;
15. CAC/payback defined;
16. conservative LTV defined;
17. support cost included;
18. AI/API variable cost included;
19. capital limits defined;
20. portfolio capacity defined;
21. cash approvals defined;
22. fake traction forbidden;
23. cold spam forbidden;
24. legal entity abstraction defined;
25. OSVČ->s.r.o. review triggers defined;
26. VAT monitoring/freshness defined;
27. regulatory trigger distinction defined;
28. agent roles defined;
29. adversarial review defined;
30. autonomy maturity defined;
31. business memory evidence rules defined;
32. experiment ledger defined;
33. monthly/quarterly governance defined;
34. App #3 selection protocol defined;
35. ŽivnoPilot not hard-wired as winner;
36. foundation explicitly contains no product implementation code.

Fail closed on missing money/legal boundaries.

---

# V1.1 — гейты приёмки после аудита

Исходные 36 пунктов сохраняются. Добавлены девять, закрывающие найденные
дефекты. PASS требует всех 45.

37. полосы решений заданы **по стадии**; на Gate 0 решение принимается по
    базовому баллу (A-1);
38. каждая цена несёт `price_basis: net | gross`; расчёты вклада идут от нетто
    (A-2);
39. стоимость поддержки выражена в CZK и имеет порог уровня kill-критерия (A-3);
40. теневая ставка разработки имеет численный дефолт и участвует в каждом
    сравнении возможностей (A-4);
41. утверждения об окупаемости запрещены до измеренного CAC (A-5);
42. у каждого гейта и у HOLD есть дедлайн по умолчанию (A-6);
43. «платящий клиент» определён так, что связанные лица, символические суммы и
    возвраты его не удовлетворяют (A-7);
44. совокупные потолки расхода существуют между «на эксперимент» и
    «инвестиционный обзор» (A-8);
45. момент старта счётчика TTR определён однозначно (A-9).

Правило «fail closed при отсутствии денежных/юридических границ» сохраняется и
распространяется на пункты 38, 39, 43 и 44.
