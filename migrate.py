from app.commands.migration import create_module_for_command

# Commandes à migrer
create_module_for_command('app/commands.py', 'handle_attachments_command', 'app/commands/document_commands/attachment.py')
create_module_for_command('app/commands.py', 'faiss_index_command', 'app/commands/document_commands/index.py')
