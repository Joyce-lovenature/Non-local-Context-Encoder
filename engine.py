from distutils.log import error
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.backends import cudnn
from models.nlcen import *
from datasets.datasets import *
from models.loss import *
from utils import *
import logging


class Engine():
    def __init__(self, config):
        self.config = config_init(config)
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        fh = logging.FileHandler(config.log_path)
        fh.setLevel(logging.INFO)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)-8s: %(message)s',
                                      datefmt="[%Y-%m-%d %H:%M:%S]")
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        logger.addHandler(fh)
        logger.addHandler(ch)
        logging.info('config: ' + str(config) + '\n')

    def _adjust_learning_rate(self, optimizer, lr):
        if lr > 1e-4:
            lr -= lr * 0.1
            for param_group in optimizer.param_groups:
                param_group['lr'] = lr
        return lr

    def train(self):
        if self.config.dataset == 'ISBI':
            model = Network_channel3()
        elif self.config.dataset == 'JPCL':
            model = Network_channel1()

        logging.info('Current model:\n' + str(model) + '\n')
        self.config = config_init(self.config)

        if torch.cuda.device_count() == 8:
            model = torch.nn.DataParallel(model, device_ids=[0, 1, 2, 3, 4, 5, 6, 7]).cuda()
        elif torch.cuda.device_count() == 4:
            model = torch.nn.DataParallel(model, device_ids=[0, 1, 2, 3]).cuda()
        else:
            model = model.cuda()

        cudnn.benchmark = True

        lr = self.config.lr
        optimizer = torch.optim.Adam(model.parameters(), lr=self.config.lr,
                                     betas=(self.config.first_momentum, self.config.second_momentum),
                                     weight_decay=self.config.weight_decay)

        dataloader = get_training_loader(self.config, self.config.batch_size, self.config.num_workers)

        for epoch in range(1, self.config.epochs+1):
            logging.info('starting epoch {}...'.format(epoch))
            # message(self.config, 'starting epoch {}...'.format(epoch))
            for i, (image, mask) in enumerate(dataloader):
                image = image.cuda()
                mask = mask.cuda()

                optimizer.zero_grad()

                out, p2_s, p3_s, p4_s, p5_s = model(image)
                # print(mask.max(), mask.min())

                loss = Loss(p2_s, p3_s, p4_s, p5_s, out, mask, self.config.lamb)
                # print(loss)

                loss.backward()
                optimizer.step()

                if i % 100 == 0:
                    logging.info('[%d/%d] Loss: %.10f' % (i, len(dataloader), loss.item()))
                    # message(self.config, '[%d/%d] Loss: %.10f' % (i, len(dataloader), loss.item()))

            lr = self._adjust_learning_rate(optimizer, lr)
            if epoch % 20 == 0 and self.config.out_to_folder == 'True':
                self.save_model(model, epoch)
        
        # show_out(out, 'out')
        
    def test(self, model=None):
        if not model:
            if self.config.dataset == 'ISBI':
                model = Network_channel3()
            elif self.config.dataset == 'JPCL':
                model = Network_channel1()
            self.config = config_init(self.config)
            try:
                model.load_state_dict(torch.load(self.config.model_path))
            except:
                raise("Cannot load model.")

        if torch.cuda.device_count() == 8:
            model = torch.nn.DataParallel(model, device_ids=[0, 1, 2, 3, 4, 5, 6, 7]).cuda()
        elif torch.cuda.device_count() == 4:
            model = torch.nn.DataParallel(model, device_ids=[0, 1, 2, 3]).cuda()
        else:
            model = model.cuda()

        cudnn.benchmark = True

        dataloader = get_testing_loader(self.config, self.config.batch_size, self.config.num_workers)

        # message(self.config, 'starting testing...')
        logging.info('starting testing...')
        errors = {'DIC': 0, 'JSC': 0}
        count = 0
        for i, (image, mask) in enumerate(dataloader):
            image = image.cuda()
            mask = mask.cuda()

            batch_size = image.size(0)
            count += batch_size
            out, _, _, _, _ = model(image)

            batch_errors = evaluate_error(out, mask)
            logging.info('[%d/%d] DIC: %.10f, JSC: %.10f' % (i, len(dataloader), batch_errors['DIC']/batch_size, batch_errors['JSC']/batch_size))
            errors['DIC'] = errors['DIC'] + batch_errors['DIC']
            errors['JSC'] = errors['JSC'] + batch_errors['JSC']
        
        errors['DIC'] = errors['DIC'] / count
        errors['JSC'] = errors['JSC'] / count

        # message(self.config, 'DIC: %.4f, JSC: %.4f' % (errors['DIC'], errors['JSC'])) 
        logging.info('DIC: %.7f, JSC: %.7f' % (errors['DIC'], errors['JSC']))
            
    def save_model(self, model, epoch):
        torch.save(model.state_dict(), '{}/model_{}.pth'.format(self.config.results_dir, epoch))
