// Plug-in property list (PiPL) for the AEGP. [verify] against the SDK's sample (e.g. Projector or Panelator).
#include "AEConfig.h"
#include "AE_EffectVers.h"

#ifndef AE_OS_WIN
	#include <AE_General.r>
#endif

resource 'PiPL' (16000) {
	{	/* array properties: 3 elements */
		/* [1] */
		Kind { AEGP },
		/* [2] */
		Name { "Voxy EasyAnimator" },
		/* [3] */
		Category { "Voxy" },
#ifdef AE_OS_WIN
	#ifdef AE_PROC_INTELx64
		CodeWin64X86 { "EntryPointFunc" },
	#endif
#else
	#ifdef AE_PROC_INTELx64
		CodeMacIntel64 { "EntryPointFunc" },
	#endif
	#ifdef AE_PROC_ARM64
		CodeMacARM64 { "EntryPointFunc" },
	#endif
#endif
	}
};
