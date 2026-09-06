import { useCallback, useEffect, useState } from "react";

import HomePage from "./pages/HomePage";
import RoomPage from "./pages/RoomPage";

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

function App() {
  const [route, setRoute] = useState<Route>(currentRoute);

  useEffect(() => {
    const onNavigate = () => setRoute(currentRoute());
    window.addEventListener("popstate", onNavigate);
    window.addEventListener("hashchange", onNavigate);
    return () => {
      window.removeEventListener("popstate", onNavigate);
      window.removeEventListener("hashchange", onNavigate);
    };
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
