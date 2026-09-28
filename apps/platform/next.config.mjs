import { withWorkflow } from 'workflow/next';
import { fileURLToPath } from 'node:url';
export default withWorkflow({outputFileTracingRoot:fileURLToPath(new URL('.',import.meta.url))});
