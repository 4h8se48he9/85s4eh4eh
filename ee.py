import os

def print_tree(folder, file, indent=""):
    try:
        items = sorted(os.listdir(folder))
    except PermissionError:
        file.write(f"{indent}[Permission Denied]\n")
        return
    
    for i, item in enumerate(items):
        path = os.path.join(folder, item)
        is_font = 'font' in item.lower()
        
        # Write the item name
        file.write(f"{indent}{item}\n")
        
        if os.path.isdir(path) and not is_font:
            print_tree(path, file, indent + "  ")
        elif os.path.isfile(path) and not is_font:
            try:
                with open(path, 'r', encoding='utf-8') as read_file:
                    content = read_file.read().strip()
                    if content:
                        for line in content.splitlines():
                            # Truncate very long lines for readability
                            if len(line) > 200:
                                line = line[:200] + "..."
                            file.write(f"{indent}  {line}\n")
                    file.write("\n")
            except (UnicodeDecodeError, IOError):
                # Skip binary files or unreadable files
                file.write(f"{indent}  [Binary or unreadable file]\n")
                file.write("\n")
            except Exception as e:
                file.write(f"{indent}  [Error reading file: {str(e)}]\n")
                file.write("\n")
        
        # Add 10 empty lines between items (except last item)
        if i < len(items) - 1:
            file.write("\n" * 10)

# Use current directory instead of script directory for better flexibility
root = os.getcwd()  # or use os.path.dirname(os.path.abspath(__file__)) for script directory

with open("folder_structure.txt", "w", encoding='utf-8') as f:
    f.write(f"Directory structure for: {root}/\n")
    f.write("=" * 50 + "\n\n")
    print_tree(root, f)

print(f"Saved to folder_structure.txt")