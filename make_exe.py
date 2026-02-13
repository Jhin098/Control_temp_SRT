import os
import shutil
import subprocess
from PIL import Image

def main():
    print("--- 🚀 Starting Auto Builder ---")

    # 1. Clean previous build
    if os.path.exists('build'):
        shutil.rmtree('build')
    if os.path.exists('dist'):
        shutil.rmtree('dist')
    print("✅ Cleaned old build folders")

    # 2. Convert Icon (icon.png -> icon.ico)
    if os.path.exists('icon.png'):
        try:
            img = Image.open('icon.png')
            img.save('icon.ico', format='ICO', sizes=[(256, 256)])
            print("✅ Converted icon.png -> icon.ico")
        except Exception as e:
            print(f"❌ Error converting icon: {e}")
            return
    elif os.path.exists('icon.ico'):
        print("⚠️ Warning: icon.png not found, using existing icon.ico (Might be corrupt if just renamed)")
    else:
        print("❌ Error: icon.png not found! Please save your image as icon.png")
        return

    # 3. Run PyInstaller
    # Using --clean to clear cache
    cmd = [
        'pyinstaller',
        '--noconfirm',
        '--clean',
        '--onefile',
        '--windowed',
        '--icon', 'icon.ico',
        '--name', 'Control_temp_SRT',
        'Temperature.py'
    ]
    
    print(f"🔨 Building EXE... ({' '.join(cmd)})")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        print("\n🎉 SUCCESS! File is at dist/Control_temp_SRT.exe")
    else:
        print("\n💀 BUILD FAILED")

if __name__ == '__main__':
    main()
