export async function resolve(specifier,context,nextResolve) {
  const result=await nextResolve(specifier,context);
  for(const cap of (process.env.SGZ_FORBIDDEN??'').split(',').filter(Boolean))
    if(result.url.includes(`/dist/${cap}/`))throw Error('disabled_module_loaded:'+cap);
  return result;
}
