// Реестр V2-страниц (контракты §8). Каждый feature-агент добавляет РОВНО две
// строки: импорт своей страницы и её имя в FEATURE_PAGES. Больше здесь ничего
// не менять — конфликт этих строк интеграция сливает тривиально.
//
// Страница экспортирует { id, title, icon, nav: 'primary'|'more', render(ctx), onEvent(ev) }
// — тот же интерфейс, что страницы MVP в ../pages.js.

import HomePage from './home.js';
import AppsPage from './apps.js';
import OverviewPage from './overview.js';
import MissionsPage from './missions.js';
import RouterPage from './router.js';
import GovernorPage from './governor.js';
import ResourcesPage from './resources.js';
import SkillsPage from './skills.js';
import TerminalPage from './terminal.js';
import BenchmarksPage from './benchmarks.js';
import BrowserPage from './browser.js';
import CodingPage from './coding.js';
import AgentMapPage from './agentmap.js';
import OrchestrasPage from './orchestras.js';
import ForksPage from './forks.js';
import HealingPage from './healing.js';
import OpenRouterPage from './openrouter.js';
import MobilePage from './mobile.js';
import BuilderPage from './builder.js';
import ImagesPage from './images.js';
import TradingLabPage from './trading_lab.js';
import MissionConsolePage from './mission_console.js';

export const FEATURE_PAGES = [
  HomePage,
  AppsPage,
  OverviewPage,
  MissionsPage,
  RouterPage,
  GovernorPage,
  ResourcesPage,
  SkillsPage,
  TerminalPage,
  BenchmarksPage,
  BrowserPage,
  CodingPage,
  AgentMapPage,
  OrchestrasPage,
  ForksPage,
  HealingPage,
  OpenRouterPage,
  MobilePage,
  BuilderPage,
  ImagesPage,
  TradingLabPage,
  MissionConsolePage,
];
