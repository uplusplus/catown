import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type PropsWithChildren,
} from "react";

type BackHandler = {
  id: symbol;
  enabled: () => boolean;
  onBack: () => boolean;
};

type BackNavigationContextValue = {
  canGoBack: () => boolean;
  triggerBack: () => boolean;
  registerBackHandler: (enabled: () => boolean, onBack: () => boolean) => () => void;
};

const BackNavigationContext = createContext<BackNavigationContextValue | null>(null);

function isEditableTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  if (target instanceof HTMLTextAreaElement) return true;
  if (target instanceof HTMLInputElement) {
    const blockedTypes = new Set([
      "button",
      "checkbox",
      "color",
      "file",
      "hidden",
      "image",
      "radio",
      "range",
      "reset",
      "submit",
    ]);
    return !blockedTypes.has((target.type || "").toLowerCase());
  }
  return false;
}

export function BackNavigationProvider({ children }: PropsWithChildren) {
  const handlersRef = useRef<BackHandler[]>([]);

  const canGoBack = useCallback(() => {
    for (let index = handlersRef.current.length - 1; index >= 0; index -= 1) {
      const handler = handlersRef.current[index];
      if (handler?.enabled()) return true;
    }
    return false;
  }, []);

  const triggerBack = useCallback(() => {
    for (let index = handlersRef.current.length - 1; index >= 0; index -= 1) {
      const handler = handlersRef.current[index];
      if (!handler?.enabled()) continue;
      if (handler.onBack()) return true;
    }
    return false;
  }, []);

  const registerBackHandler = useCallback((enabled: () => boolean, onBack: () => boolean) => {
    const handler: BackHandler = {
      id: Symbol("back-handler"),
      enabled,
      onBack,
    };
    handlersRef.current.push(handler);

    return () => {
      handlersRef.current = handlersRef.current.filter((entry) => entry.id !== handler.id);
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented || event.isComposing) return;
      if (event.key !== "Escape" && event.code !== "Escape") return;
      if (isEditableTarget(event.target)) return;
      if (!triggerBack()) return;
      event.preventDefault();
      event.stopPropagation();
    }

    document.addEventListener("keydown", handleKeyDown, true);
    return () => document.removeEventListener("keydown", handleKeyDown, true);
  }, [triggerBack]);

  const value = useMemo<BackNavigationContextValue>(
    () => ({
      canGoBack,
      triggerBack,
      registerBackHandler,
    }),
    [canGoBack, registerBackHandler, triggerBack],
  );

  return <BackNavigationContext.Provider value={value}>{children}</BackNavigationContext.Provider>;
}

export function useBackNavigation() {
  const value = useContext(BackNavigationContext);
  if (!value) {
    throw new Error("useBackNavigation must be used within BackNavigationProvider.");
  }
  return value;
}

export function useRegisterBackHandler(enabled: () => boolean, onBack: () => boolean) {
  const { registerBackHandler } = useBackNavigation();
  const enabledRef = useRef(enabled);
  const onBackRef = useRef(onBack);

  enabledRef.current = enabled;
  onBackRef.current = onBack;

  useEffect(
    () =>
      registerBackHandler(
        () => enabledRef.current(),
        () => onBackRef.current(),
      ),
    [registerBackHandler],
  );
}
