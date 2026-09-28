-- Event trigger is maintained by postgres, not callable by API users.
revoke execute on function public.rls_auto_enable() from public, anon, authenticated;
