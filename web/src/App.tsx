import { useCallback, useEffect, useState } from "react";

import { loadRoomSession } from "./engine/room-persistence";
import HomePage from "./pages/HomePage";
import RoomPage from "./pages/RoomPage";
import { useSessionStore } from "./state/session-store";

/**
 * Manual two-page routing (docs/adr/007-react-typescript-client.md's "Redux Toolkit:
 * heavier than needed" reasoning applies just as well to a router library here -- the
 * whole app is `/` and `/r#<code>`, the share-link route (docs/protocol.md §1: the
 * code lives in the URL fragment so it never reaches server or proxy logs).
 */
interface Route {
  path: string;
  hash: string;
}

function currentRoute(): Route {
  return { path: window.location.pathname, hash: window.location.hash.replace(/^#/, "") };
}

/** A page reload while in a room: land back on `/r#<code>` using the persisted room
 * session (docs/architecture.md §4.3), rather than the plain "join by code" prompt a
 * bare `/` reload would otherwise show. Read once, as `useState`'s initializer, since
 * a route correction belongs in the initial render, not a `setState` dispatched from
 * an effect after it. */
function initialRoute(): Route {
  const route = currentRoute();
  if (route.path === "/r") return route;
  const persisted = loadRoomSession();
  if (!persisted) return route;
  window.history.replaceState(null, "", `/r#${persisted.code ?? ""}`);
  return currentRoute();
}

function App() {
  const [route, setRoute] = useState<Route>(initialRoute);

  useEffect(() => {
    const onNavigate = () => setRoute(currentRoute());
    window.addEventListener("popstate", onNavigate);
    window.addEventListener("hashchange", onNavigate);
    return () => {
      window.removeEventListener("popstate", onNavigate);
      window.removeEventListener("hashchange", onNavigate);
    };
  }, []);

  // Reconnect using the persisted room token (docs/architecture.md §4.3) instead of
  // the REST create/join flow, which would ask the server to join as a brand-new
  // peer. A no-op if `initialRoute()` above found nothing to resume.
  useEffect(() => {
    void useSessionStore.getState().resume();
  }, []);

  const goToRoom = useCallback((code: string) => {
    window.history.pushState(null, "", `/r#${code}`);
    setRoute(currentRoute());
  }, []);

  const goHome = useCallback(() => {
    window.history.pushState(null, "", "/");
    setRoute(currentRoute());
  }, []);

  if (route.path === "/r") {
    return <RoomPage joinCode={route.hash || null} onLeave={goHome} />;
  }
  return <HomePage onRoomReady={goToRoom} />;
}

export default App;
