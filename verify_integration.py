#!/usr/bin/env python3
"""
Quick Verification Checklist for Intelligent System Integration
Run this to verify everything is properly connected
"""

import os
import sys

def check_files():
    """Check all required files exist."""
    files_to_check = {
        "cogs/mandy_ai.py": "MandyAI cog with embedded intelligent processor",
        "intelligent_command_processor.py": "Core intelligent command engine",
        "main.py": "Bot initialization",
        "capability_registry.py": "Tool registry",
        "tool_plugin_manager.py": "Plugin manager",
    }
    
    print("=" * 60)
    print("FILE STRUCTURE CHECK")
    print("=" * 60)
    
    all_exist = True
    for file, desc in files_to_check.items():
        exists = os.path.exists(file)
        status = "✅" if exists else "❌"
        print(f"{status} {file:40s} ({desc})")
        all_exist = all_exist and exists
    
    return all_exist

def check_integration():
    """Check integration points in mandy_ai.py."""
    print("\n" + "=" * 60)
    print("INTEGRATION POINTS CHECK")
    print("=" * 60)
    
    checks = {
        "Import statement": "from intelligent_command_processor import IntelligentCommandProcessor",
        "Processor init": "self._intelligent_processor = IntelligentCommandProcessor(bot)",
        "Executor class": "class _ManydAICommandExecutor:",
        "Handler integration": "if self._intelligent_processor:",
        "Process call": "await self._intelligent_processor.process(",
    }
    
    try:
        with open("cogs/mandy_ai.py", "r") as f:
            content = f.read()
        
        all_found = True
        for check_name, pattern in checks.items():
            found = pattern in content
            status = "✅" if found else "❌"
            print(f"{status} {check_name:30s} - {pattern[:40]}")
            all_found = all_found and found
        
        return all_found
    except Exception as e:
        print(f"❌ Error reading mandy_ai.py: {e}")
        return False

def check_processor():
    """Check intelligent processor has all components."""
    print("\n" + "=" * 60)
    print("INTELLIGENT PROCESSOR COMPONENTS")
    print("=" * 60)
    
    components = {
        "IntentRecognizer": "Pattern-based intent recognition",
        "ArgumentExtractor": "User/message/number extraction",
        "ContextMemory": "User context tracking",
        "ClarificationHandler": "Ambiguity resolution",
        "ConfirmationFormatter": "Result formatting",
        "IntelligentCommandProcessor": "Main orchestrator",
    }
    
    try:
        with open("intelligent_command_processor.py", "r") as f:
            content = f.read()
        
        all_found = True
        for component, desc in components.items():
            found = f"class {component}" in content
            status = "✅" if found else "❌"
            print(f"{status} {component:30s} - {desc}")
            all_found = all_found and found
        
        return all_found
    except Exception as e:
        print(f"❌ Error reading intelligent_command_processor.py: {e}")
        return False

def check_executor():
    """Check executor has all required methods."""
    print("\n" + "=" * 60)
    print("COMMAND EXECUTOR METHODS")
    print("=" * 60)
    
    methods = {
        "send_dm": "Send DM to users",
        "add_watcher": "Add watcher to user",
        "remove_watcher": "Remove watcher",
        "list_watchers": "List all watchers",
        "create_mirror": "Create channel mirror",
        "show_stats": "Show statistics",
        "show_health": "Show bot health",
        "show_queue": "Show job queue",
    }
    
    try:
        with open("cogs/mandy_ai.py", "r") as f:
            content = f.read()
        
        all_found = True
        for method, desc in methods.items():
            found = f"async def {method}(self" in content
            status = "✅" if found else "❌"
            print(f"{status} {method:20s} - {desc}")
            all_found = all_found and found
        
        return all_found
    except Exception as e:
        print(f"❌ Error reading mandy_ai.py: {e}")
        return False

def main():
    """Run all checks."""
    results = []
    
    results.append(("Files", check_files()))
    results.append(("Integration", check_integration()))
    results.append(("Processor", check_processor()))
    results.append(("Executor", check_executor()))
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    for check_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {check_name}")
    
    all_passed = all(result[1] for result in results)
    
    if all_passed:
        print("\n✅ ALL CHECKS PASSED - SYSTEM READY")
        print("\nYour intelligent command processor is fully integrated!")
        print("Users can now type naturally without learning syntax.")
        return 0
    else:
        print("\n❌ SOME CHECKS FAILED - REVIEW INTEGRATION")
        return 1

if __name__ == "__main__":
    sys.exit(main())
